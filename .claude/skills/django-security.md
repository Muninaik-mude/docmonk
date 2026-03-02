---
name: django-security
description: >
  Apply this skill when implementing authentication, authorization, JWT tokens,
  permissions, rate limiting, or any security-sensitive feature in Django/DRF.
---

# Django Security & Authentication

## Core Philosophy
- **Deny by default** — start with no access, grant explicitly
- **Authenticate first, authorize second** — these are different things
- **Never trust client data** — validate everything at the serializer level
- **Least privilege** — users and services get only what they need

---

## JWT Setup (SimpleJWT)

### Settings
```python
from datetime import timedelta

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=15),   # Short — rotate often
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,                  # Invalidate old refresh tokens
    'AUTH_HEADER_TYPES': ('Bearer',),
    'TOKEN_OBTAIN_SERIALIZER': 'apps.users.serializers.CustomTokenObtainSerializer',
}

INSTALLED_APPS += ['rest_framework_simplejwt.token_blacklist']
```

### Auth URLs
```python
from rest_framework_simplejwt.views import TokenRefreshView
from .views import CustomTokenObtainView, LogoutView

urlpatterns = [
    path('auth/login/', CustomTokenObtainView.as_view(), name='token_obtain'),
    path('auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('auth/logout/', LogoutView.as_view(), name='logout'),
    path('auth/register/', RegisterView.as_view(), name='register'),
]
```

### Custom Token (embed user data)
```python
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView


class CustomTokenObtainSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        # Add custom claims — accessible without DB hit
        token['email'] = user.email
        token['role'] = user.role
        token['full_name'] = user.get_full_name()
        return token


class CustomTokenObtainView(TokenObtainPairView):
    serializer_class = CustomTokenObtainSerializer
```

### Logout (blacklist refresh token)
```python
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh_token = request.data.get('refresh')
        if not refresh_token:
            return Response({'detail': 'Refresh token required.'}, status=400)
        try:
            token = RefreshToken(refresh_token)
            token.blacklist()
        except TokenError:
            return Response({'detail': 'Invalid or expired token.'}, status=400)
        return Response({'detail': 'Logged out successfully.'})
```

---

## Custom Permissions

```python
# apps/core/permissions.py
from rest_framework.permissions import BasePermission, SAFE_METHODS


class IsOwnerOrAdmin(BasePermission):
    """Allow owners or admins. Read-only for others."""

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        if request.user.is_staff:
            return True
        # Adapt 'owner' to your model's user field name
        owner_field = getattr(obj, 'owner', None) or getattr(obj, 'user', None) or getattr(obj, 'customer', None)
        return owner_field == request.user


class IsAdminUser(BasePermission):
    """Strictly admin only."""
    def has_permission(self, request, view):
        return request.user and request.user.is_staff


class ReadOnly(BasePermission):
    """Allow GET, HEAD, OPTIONS only."""
    def has_permission(self, request, view):
        return request.method in SAFE_METHODS
```

### Using permissions on ViewSets
```python
from rest_framework.permissions import IsAuthenticated, IsAdminUser, AllowAny
from apps.core.permissions import IsOwnerOrAdmin


class ProductViewSet(viewsets.ModelViewSet):
    
    def get_permissions(self):
        """Different permissions per action."""
        if self.action in ['list', 'retrieve']:
            return [AllowAny()]
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [IsAuthenticated(), IsAdminUser()]
        return [IsAuthenticated()]
```

---

## Role-Based Access Control (RBAC)

```python
# Simple role field on User model
class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = 'admin', 'Admin'
        MANAGER = 'manager', 'Manager'
        STAFF = 'staff', 'Staff'
        CUSTOMER = 'customer', 'Customer'

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.CUSTOMER)

    @property
    def is_manager_or_above(self):
        return self.role in [self.Role.ADMIN, self.Role.MANAGER]


# Permission class using roles
class IsManagerOrAbove(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.is_manager_or_above
```

---

## Rate Limiting

```python
# Custom rate limits per view
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class LoginRateThrottle(AnonRateThrottle):
    rate = '5/minute'  # Prevent brute force


class PasswordResetThrottle(AnonRateThrottle):
    rate = '3/hour'


# Apply to view
class LoginView(TokenObtainPairView):
    throttle_classes = [LoginRateThrottle]
```

---

## Input Validation Patterns

```python
class RegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)
    password_confirm = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ['email', 'first_name', 'last_name', 'password', 'password_confirm']

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Email already registered.")
        return value.lower()

    def validate_password(self, value):
        from django.contrib.auth.password_validation import validate_password
        validate_password(value)
        return value

    def validate(self, attrs):
        if attrs['password'] != attrs.pop('password_confirm'):
            raise serializers.ValidationError({"password": "Passwords do not match."})
        return attrs

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)
```

---

## Security Settings Checklist (production.py)

```python
# HTTPS
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# CORS
CORS_ALLOWED_ORIGINS = env.list('CORS_ALLOWED_ORIGINS')  # Explicit list, never wildcard in prod
CORS_ALLOW_CREDENTIALS = True

# Content security
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'

# Debug must be False
DEBUG = False
```

---

## Rules Claude Must Follow
- NEVER set `permission_classes = []` or `AllowAny` on write endpoints
- ALWAYS use `write_only=True` on password fields in serializers
- NEVER store passwords in plaintext — always `create_user()` not `create()`
- ALWAYS set short JWT access token lifetimes (15 min max)
- ALWAYS blacklist refresh tokens on logout
- NEVER put secrets in code — use `django-environ`
- ALWAYS validate + sanitize all user inputs in serializers
- NEVER expose Django admin on `/admin/` in production — change the URL
- ALWAYS use HTTPS in production — enforce with HSTS