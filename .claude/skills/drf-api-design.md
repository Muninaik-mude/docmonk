---
name: drf-api-design
description: >
  Apply this skill when building DRF serializers, views, viewsets, routers,
  or designing any REST API endpoint. Enforces production patterns for
  request/response design, error handling, and API contracts.
---

# DRF API Design

## Core Philosophy
- **Serializers are contracts** — they define your public API shape, treat them seriously
- **ViewSets over APIViews** — less repetition, more consistency
- **Services handle logic** — views orchestrate, services execute
- **Fail loudly** — explicit error responses with codes, not vague 500s

---

## Serializer Patterns

### Standard Serializer Structure
```python
from rest_framework import serializers
from .models import Order


class OrderListSerializer(serializers.ModelSerializer):
    """Lightweight — used in list endpoints."""
    class Meta:
        model = Order
        fields = ['id', 'status', 'total_amount', 'created_at']
        read_only_fields = ['id', 'created_at']


class OrderDetailSerializer(serializers.ModelSerializer):
    """Full detail — used in retrieve/create/update."""
    customer_name = serializers.SerializerMethodField()
    items = OrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = ['id', 'customer_name', 'items', 'status',
                  'total_amount', 'notes', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_customer_name(self, obj):
        return obj.customer.get_full_name()

    def validate_total_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Amount must be positive.")
        return value

    def validate(self, attrs):
        # Cross-field validation here
        return attrs


class OrderCreateSerializer(serializers.ModelSerializer):
    """Write-only — separate serializer for creation."""
    class Meta:
        model = Order
        fields = ['customer', 'items', 'notes']

    def create(self, validated_data):
        items_data = validated_data.pop('items')
        order = Order.objects.create(**validated_data)
        # delegate complex creation to service
        from .services import OrderService
        OrderService.attach_items(order, items_data)
        return order
```

### Rule: Use different serializers per action
```python
# In ViewSet
def get_serializer_class(self):
    if self.action == 'list':
        return OrderListSerializer
    if self.action == 'create':
        return OrderCreateSerializer
    return OrderDetailSerializer
```

---

## ViewSet Patterns

### Standard ViewSet
```python
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from .models import Order
from .serializers import OrderListSerializer, OrderDetailSerializer, OrderCreateSerializer
from .services import OrderService
from apps.core.permissions import IsOwnerOrAdmin


class OrderViewSet(viewsets.ModelViewSet):
    """
    CRUD for Orders.
    list:   GET  /api/v1/orders/
    create: POST /api/v1/orders/
    retrieve: GET /api/v1/orders/{id}/
    update: PUT  /api/v1/orders/{id}/
    partial_update: PATCH /api/v1/orders/{id}/
    destroy: DELETE /api/v1/orders/{id}/
    """
    permission_classes = [IsAuthenticated, IsOwnerOrAdmin]
    filterset_fields = ['status']
    search_fields = ['customer__email', 'notes']
    ordering_fields = ['created_at', 'total_amount']
    ordering = ['-created_at']

    def get_queryset(self):
        # Always scope to current user unless admin
        if self.request.user.is_staff:
            return Order.objects.select_related('customer').prefetch_related('items').all()
        return Order.objects.select_related('customer').prefetch_related('items').filter(
            customer=self.request.user
        )

    def get_serializer_class(self):
        if self.action == 'list':
            return OrderListSerializer
        if self.action == 'create':
            return OrderCreateSerializer
        return OrderDetailSerializer

    def perform_create(self, serializer):
        # Inject authenticated user automatically
        serializer.save(customer=self.request.user)

    @action(detail=True, methods=['post'], url_path='cancel')
    def cancel(self, request, pk=None):
        """POST /api/v1/orders/{id}/cancel/"""
        order = self.get_object()
        try:
            updated_order = OrderService.cancel_order(order, cancelled_by=request.user)
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(OrderDetailSerializer(updated_order).data)

    @action(detail=False, methods=['get'], url_path='my-summary')
    def my_summary(self, request):
        """GET /api/v1/orders/my-summary/"""
        data = OrderService.get_user_summary(request.user)
        return Response(data)
```

---

## Router Configuration (urls.py)

```python
from rest_framework.routers import DefaultRouter
from .views import OrderViewSet

router = DefaultRouter()
router.register(r'orders', OrderViewSet, basename='order')

urlpatterns = router.urls
```

---

## Standard Response Format

Always return consistent response shapes. Create a helper:

```python
# apps/core/responses.py

from rest_framework.response import Response


def success_response(data=None, message="Success", status_code=200, **kwargs):
    return Response({
        "success": True,
        "message": message,
        "data": data,
    }, status=status_code, **kwargs)


def error_response(message="An error occurred", errors=None, status_code=400):
    return Response({
        "success": False,
        "message": message,
        "errors": errors or {},
    }, status=status_code)
```

---

## Custom Exception Handler (apps/core/exceptions.py)

```python
from rest_framework.views import exception_handler
from rest_framework.response import Response


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)

    if response is not None:
        response.data = {
            "success": False,
            "message": "Request failed",
            "errors": response.data,
        }

    return response
```

Register in settings: `'EXCEPTION_HANDLER': 'apps.core.exceptions.custom_exception_handler'`

---

## Service Layer Pattern (services.py)

```python
# apps/orders/services.py

from django.db import transaction
from .models import Order


class OrderService:

    @staticmethod
    @transaction.atomic
    def cancel_order(order: Order, cancelled_by) -> Order:
        """Business logic belongs here, not in views."""
        if order.status not in ['pending', 'processing']:
            raise ValueError(f"Cannot cancel order in '{order.status}' status.")

        order.status = 'cancelled'
        order.cancelled_by = cancelled_by
        order.save(update_fields=['status', 'cancelled_by', 'updated_at'])

        # Trigger signals, send emails, etc. here
        return order

    @staticmethod
    def get_user_summary(user) -> dict:
        from django.db.models import Sum, Count
        return Order.objects.filter(customer=user).aggregate(
            total_orders=Count('id'),
            total_spent=Sum('total_amount'),
        )
```

---

## OpenAPI Documentation

```python
# Use drf-spectacular decorators on non-obvious endpoints
from drf_spectacular.utils import extend_schema, OpenApiParameter

@extend_schema(
    summary="Cancel an order",
    description="Cancels a pending or processing order. Cannot cancel shipped orders.",
    responses={200: OrderDetailSerializer, 400: 'Error details'},
)
@action(detail=True, methods=['post'])
def cancel(self, request, pk=None):
    ...
```

---

## Rules Claude Must Follow
- ALWAYS use `select_related`/`prefetch_related` in `get_queryset` — never lazy load in serializers
- NEVER put `if/else` business logic inside views — call a service method
- ALWAYS use separate serializers for list vs detail vs create/update
- ALWAYS scope querysets to the current user by default
- NEVER return raw model data without going through a serializer
- ALWAYS use `update_fields` in `.save()` when updating specific fields
- ALWAYS wrap multi-step DB writes in `@transaction.atomic`
- ALWAYS use `perform_create` / `perform_update` to inject context (user, org, etc.)