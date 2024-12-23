from rest_framework import generics, status
from rest_framework.response import Response
import logging
from django.contrib.auth.hashers import check_password
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from django.core.mail import send_mail
from django.conf import settings
from django.utils.encoding import force_bytes, force_str
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.shortcuts import render

from .models import Event, Currency, User  # Ensure this import is correct
from .serializers import EventSerializer, CurrencySerializer

logger = logging.getLogger(__name__)
# Create your views here.

class EventList(generics.ListCreateAPIView):
    queryset = Event.objects.all()
    serializer_class = EventSerializer

    def get(self, request, *args, **kwargs):
        logger.debug("Received GET request")
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        logger.debug(f"Received POST data: {request.data}")
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            self.perform_create(serializer)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        logger.error(f"Validation errors: {serializer.errors}")
        return Response(
            {
                "error": "Invalid data",
                "details": serializer.errors
            }, 
            status=status.HTTP_400_BAD_REQUEST
        )

    def delete(self, request, *args, **kwargs):
        event_id = kwargs.get('pk')
        logger.debug(f"Attempting to delete event with ID: {event_id}")
        
        try:
            event = Event.objects.get(pk=event_id)
            event.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Event.DoesNotExist:
            logger.error(f"Event with ID {event_id} not found")
            return Response(
                {"error": "Event not found"}, 
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.error(f"Error deleting event: {str(e)}")
            return Response(
                {"error": "Failed to delete event"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

class CurrencyList(generics.ListCreateAPIView):
    # get only currency name for GET requests
    def get_queryset(self):
        if self.request.method == 'GET':
            return Currency.objects.values('name')
        return Currency.objects.all()
    serializer_class = CurrencySerializer

class UserAuthentication(APIView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        username = request.data.get('username')
        password = request.data.get('password')
        try:
            user = User.objects.get(name=username)
            if check_password(password, user.password):
                return Response({"message": "Authentication successful"}, status=status.HTTP_200_OK)
            else:
                return Response({"error": "Invalid password"}, status=status.HTTP_400_BAD_REQUEST)
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)

class PasswordResetRequest(APIView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        email = request.data.get('email')
        try:
            user = User.objects.get(email=email)
            # Use custom token generation instead of default_token_generator
            token = self.generate_token(user)
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            reset_url = f"{request.scheme}://{request.get_host()}/api/v1/reset-password/{uid}/{token}"
            send_mail(
                'Запрос на сброс пароля',
                f'Для сброса пароля перейдите по ссылке: {reset_url}',
                settings.DEFAULT_FROM_EMAIL,
                [email],
                fail_silently=False
            )
            return Response({"message": "Password reset link sent"}, status=status.HTTP_200_OK)
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)

    def generate_token(self, user):
        from hashlib import sha256
        from django.utils import timezone
        # Token valid for 24 hours
        timestamp = int(timezone.now().timestamp())
        # Round timestamp to hours to give 1-hour validity window
        timestamp_hours = timestamp - (timestamp % 3600)
        token_string = f"{user.email}-{user.id}-{user.password}-{timestamp_hours}"
        return sha256(token_string.encode()).hexdigest()[:32]

class PasswordResetConfirm(APIView):
    permission_classes = [AllowAny]
    template_name = 'password_reset_confirm.html'

    def get(self, request, uidb64, token):
        try:
            uid = force_str(urlsafe_base64_decode(uidb64))
            user = User.objects.get(pk=uid)
            # Verify token and check expiration
            if self.check_token(user, token):
                return render(request, self.template_name, {
                    'validlink': True,
                    'uidb64': uidb64,
                    'token': token
                })
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            pass
        return render(request, self.template_name, {'validlink': False})

    def post(self, request, uidb64, token, *args, **kwargs):
        try:
            uid = force_str(urlsafe_base64_decode(uidb64))
            user = User.objects.get(pk=uid)
            if self.check_token(user, token):
                password1 = request.POST.get('new_password1')
                password2 = request.POST.get('new_password2')
                
                if not password1 or not password2:
                    return render(request, self.template_name, {
                        'validlink': True,
                        'error': 'Please enter both passwords',
                        'token': token,
                        'uidb64': uidb64
                    })
                
                if password1 != password2:
                    return render(request, self.template_name, {
                        'validlink': True,
                        'error': 'Passwords do not match',
                        'token': token,
                        'uidb64': uidb64
                    })
                
                if len(password1) < 8:
                    return render(request, self.template_name, {
                        'validlink': True,
                        'error': 'Пароль должен содержать не менее 8 символов',
                        'token': token,
                        'uidb64': uidb64
                    })
                
                user.set_password(password1)
                user.save()
                return render(request, self.template_name, {
                    'validlink': True,
                    'success': True
                })
                
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            pass
            
        return render(request, self.template_name, {'validlink': False})

    def check_token(self, user, token):
        # Recreate token and verify it matches
        from hashlib import sha256
        from django.utils import timezone
        try:
            current_time = int(timezone.now().timestamp())
            # Check last 24 hours in 1-hour intervals
            for hours in range(24):
                check_time = current_time - (current_time % 3600) - (hours * 3600)
                token_string = f"{user.email}-{user.id}-{user.password}-{check_time}"
                expected_token = sha256(token_string.encode()).hexdigest()[:32]
                if token == expected_token:
                    return True
            return False
        except Exception:
            return False
