---
name: django-testing
description: >
  Apply this skill when writing tests for Django/DRF apps — models, views,
  serializers, services, or permissions. Enforces pytest patterns, factory-based
  test data, and comprehensive API testing habits.
---

# Django Testing

## Core Philosophy
- **Test behavior, not implementation** — test what it does, not how
- **Factories, not fixtures** — dynamic, composable test data
- **Isolate units** — mock external services, test your code
- **Coverage is a floor, not a ceiling** — 80%+ minimum, but quality over quantity

---

## Setup

### pytest.ini
```ini
[pytest]
DJANGO_SETTINGS_MODULE = config.settings.testing
python_files = tests.py test_*.py *_test.py
python_classes = Test*
python_functions = test_*
addopts = -v --tb=short --reuse-db
```

### settings/testing.py
```python
from .base import *

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.MD5PasswordHasher',  # Fast hashing for tests
]

EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
CELERY_TASK_ALWAYS_EAGER = True  # Run tasks synchronously in tests
DEFAULT_FILE_STORAGE = 'django.core.files.storage.InMemoryStorage'
```

### requirements/testing.txt
```
pytest
pytest-django
pytest-factoryboy
factory-boy
faker
pytest-cov
```

---

## Factory Pattern

```python
# apps/users/tests/factories.py
import factory
from faker import Faker
from django.contrib.auth import get_user_model

fake = Faker()
User = get_user_model()


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User

    email = factory.LazyFunction(lambda: fake.unique.email())
    username = factory.LazyAttribute(lambda o: o.email.split('@')[0])
    first_name = factory.LazyFunction(fake.first_name)
    last_name = factory.LazyFunction(fake.last_name)
    password = factory.PostGenerationMethodCall('set_password', 'testpass123')
    is_active = True
    role = 'customer'

    class Params:
        admin = factory.Trait(is_staff=True, is_superuser=True, role='admin')
        manager = factory.Trait(role='manager')


# apps/orders/tests/factories.py
class OrderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Order

    customer = factory.SubFactory(UserFactory)
    status = 'pending'
    total_amount = factory.LazyFunction(lambda: fake.pydecimal(left_digits=4, right_digits=2, positive=True))
    notes = factory.LazyFunction(fake.sentence)
```

---

## Model Tests

```python
# apps/orders/tests/test_models.py
import pytest
from .factories import OrderFactory


@pytest.mark.django_db
class TestOrderModel:

    def test_str_representation(self):
        order = OrderFactory(status='pending')
        assert 'pending' in str(order).lower() or str(order.id) in str(order)

    def test_default_status_is_pending(self):
        order = OrderFactory()
        assert order.status == 'pending'

    def test_created_at_set_automatically(self):
        order = OrderFactory()
        assert order.created_at is not None

    def test_uuid_primary_key(self):
        import uuid
        order = OrderFactory()
        assert isinstance(order.id, uuid.UUID)
```

---

## Service Tests

```python
# apps/orders/tests/test_services.py
import pytest
from .factories import OrderFactory, UserFactory
from apps.orders.services import OrderService


@pytest.mark.django_db
class TestOrderService:

    def test_cancel_pending_order_succeeds(self):
        order = OrderFactory(status='pending')
        user = UserFactory()
        
        result = OrderService.cancel_order(order, cancelled_by=user)
        
        assert result.status == 'cancelled'
        order.refresh_from_db()
        assert order.status == 'cancelled'

    def test_cancel_shipped_order_raises_error(self):
        order = OrderFactory(status='shipped')
        user = UserFactory()
        
        with pytest.raises(ValueError, match="Cannot cancel"):
            OrderService.cancel_order(order, cancelled_by=user)

    def test_get_user_summary_returns_correct_counts(self):
        user = UserFactory()
        OrderFactory.create_batch(3, customer=user, status='completed')
        OrderFactory.create_batch(1, customer=user, status='cancelled')
        OrderFactory()  # different user — should not count

        result = OrderService.get_user_summary(user)
        
        assert result['total_orders'] == 4
```

---

## API / View Tests

```python
# apps/orders/tests/test_views.py
import pytest
from rest_framework.test import APIClient
from rest_framework import status
from .factories import OrderFactory, UserFactory


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def auth_client(api_client):
    user = UserFactory()
    api_client.force_authenticate(user=user)
    api_client._user = user
    return api_client


@pytest.fixture
def admin_client(api_client):
    admin = UserFactory(admin=True)
    api_client.force_authenticate(user=admin)
    return api_client


@pytest.mark.django_db
class TestOrderListView:

    def test_unauthenticated_returns_401(self, api_client):
        response = api_client.get('/api/v1/orders/')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_user_sees_only_own_orders(self, auth_client):
        user = auth_client._user
        own_orders = OrderFactory.create_batch(3, customer=user)
        OrderFactory.create_batch(2)  # other users' orders
        
        response = auth_client.get('/api/v1/orders/')
        
        assert response.status_code == status.HTTP_200_OK
        assert response.data['count'] == 3

    def test_admin_sees_all_orders(self, admin_client):
        OrderFactory.create_batch(5)
        
        response = admin_client.get('/api/v1/orders/')
        
        assert response.status_code == status.HTTP_200_OK
        assert response.data['count'] == 5

    def test_list_is_paginated(self, auth_client):
        user = auth_client._user
        OrderFactory.create_batch(25, customer=user)
        
        response = auth_client.get('/api/v1/orders/')
        
        assert 'next' in response.data
        assert len(response.data['results']) == 20  # default page size


@pytest.mark.django_db
class TestOrderCancelAction:

    def test_cancel_own_pending_order(self, auth_client):
        user = auth_client._user
        order = OrderFactory(customer=user, status='pending')
        
        response = auth_client.post(f'/api/v1/orders/{order.id}/cancel/')
        
        assert response.status_code == status.HTTP_200_OK
        assert response.data['status'] == 'cancelled'

    def test_cannot_cancel_other_users_order(self, auth_client):
        order = OrderFactory(status='pending')  # different user
        
        response = auth_client.post(f'/api/v1/orders/{order.id}/cancel/')
        
        assert response.status_code in [
            status.HTTP_403_FORBIDDEN, 
            status.HTTP_404_NOT_FOUND
        ]

    def test_cannot_cancel_shipped_order(self, auth_client):
        user = auth_client._user
        order = OrderFactory(customer=user, status='shipped')
        
        response = auth_client.post(f'/api/v1/orders/{order.id}/cancel/')
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'Cannot cancel' in response.data['detail']
```

---

## Serializer Tests

```python
# apps/orders/tests/test_serializers.py
import pytest
from apps.orders.serializers import OrderCreateSerializer
from .factories import UserFactory


@pytest.mark.django_db
class TestOrderCreateSerializer:

    def test_valid_data_passes(self):
        user = UserFactory()
        data = {'notes': 'Please deliver before noon.'}
        
        serializer = OrderCreateSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_negative_amount_fails_validation(self):
        data = {'total_amount': -10}
        
        serializer = OrderCreateSerializer(data=data)
        assert not serializer.is_valid()
        assert 'total_amount' in serializer.errors
```

---

## Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=apps --cov-report=html

# Run specific app
pytest apps/orders/

# Run specific test class
pytest apps/orders/tests/test_views.py::TestOrderCancelAction

# Show SQL queries during test (debug)
pytest -s --ds=config.settings.testing -p no:warnings
```

---

## Rules Claude Must Follow
- ALWAYS use `@pytest.mark.django_db` on tests that hit the database
- ALWAYS use factories, never create model instances manually with `.objects.create()` in tests
- NEVER use production settings in tests — always `testing.py`
- ALWAYS test both success and failure/edge cases
- ALWAYS test permission boundaries — unauthenticated, wrong user, admin
- NEVER mock the database in tests — use in-memory SQLite
- ALWAYS test API responses for structure, not just status codes
- ALWAYS use `force_authenticate` in API tests, not actual JWT flow (that's integration testing)