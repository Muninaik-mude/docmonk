---
name: django-performance
description: >
  Apply this skill when optimizing Django queries, implementing caching,
  setting up pagination, designing background tasks, or reviewing any
  code path that handles significant data volume.
---

# Django Performance & Optimization

## Core Philosophy
- **Measure before you optimize** — profile first, guess never
- **Database is the bottleneck** — minimize queries, not code lines
- **Cache aggressively** — but invalidate correctly
- **Async for I/O** — use Celery for anything that blocks a request

---

## Query Optimization

### Query counting in tests/development
```python
# settings/development.py
LOGGING = {
    'version': 1,
    'handlers': {
        'console': {'class': 'logging.StreamHandler'},
    },
    'loggers': {
        'django.db.backends': {
            'handlers': ['console'],
            'level': 'DEBUG',  # Prints every SQL query
        },
    },
}
```

### Use `connection.queries` for debugging
```python
from django.db import connection, reset_queries
reset_queries()
# ... run your code ...
print(f"Queries: {len(connection.queries)}")
for q in connection.queries:
    print(q['sql'])
```

### `only()` and `defer()` — fetch what you need
```python
# Only fetch fields needed for list view
products = Product.objects.only('id', 'name', 'price', 'slug').filter(is_active=True)

# Defer expensive JSON/text fields
products = Product.objects.defer('description', 'metadata').all()
```

### `exists()` vs `count()` vs `bool()`
```python
# BAD — fetches entire queryset
if Product.objects.filter(slug=slug):  
    ...

# GOOD — single SQL EXISTS
if Product.objects.filter(slug=slug).exists():
    ...

# BAD for boolean check
count = Product.objects.filter(is_active=True).count()
if count > 0:

# GOOD
if Product.objects.filter(is_active=True).exists():
    ...
```

### Avoid repeated DB hits for same data
```python
# BAD
class OrderSerializer(serializers.ModelSerializer):
    item_count = serializers.SerializerMethodField()
    total = serializers.SerializerMethodField()

    def get_item_count(self, obj):
        return obj.items.count()  # DB hit

    def get_total(self, obj):
        return obj.items.aggregate(Sum('price'))['price__sum']  # DB hit

# GOOD — annotate in queryset, read in serializer
queryset = Order.objects.annotate(
    item_count=Count('items'),
    total=Sum('items__price'),
)

class OrderSerializer(serializers.ModelSerializer):
    item_count = serializers.IntegerField(read_only=True)
    total = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
```

---

## Pagination

### Standard Pagination (apps/core/pagination.py)
```python
from rest_framework.pagination import PageNumberPagination, CursorPagination


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class CursorPaginationByCreated(CursorPagination):
    """Better for large datasets — no OFFSET, uses cursor."""
    page_size = 20
    ordering = '-created_at'
    cursor_query_param = 'cursor'
```

Use `CursorPagination` for large tables — `PageNumberPagination` with high page numbers causes full table scans.

---

## Caching

### Redis cache setup
```python
# settings/base.py
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': env('REDIS_URL', default='redis://127.0.0.1:6379/1'),
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
        },
        'TIMEOUT': 300,  # 5 minutes default
    }
}
```

### View-level caching
```python
from django.core.cache import cache


class CategoryListView(generics.ListAPIView):
    serializer_class = CategorySerializer
    permission_classes = [AllowAny]

    def list(self, request, *args, **kwargs):
        cache_key = 'category_list'
        cached = cache.get(cache_key)
        if cached:
            return Response(cached)
        
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        cache.set(cache_key, serializer.data, timeout=3600)  # 1 hour
        return Response(serializer.data)
```

### Cache invalidation on save
```python
# apps/catalog/signals.py
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.core.cache import cache
from .models import Category


@receiver([post_save, post_delete], sender=Category)
def invalidate_category_cache(sender, **kwargs):
    cache.delete('category_list')
    cache.delete_pattern('category_*')  # requires django-redis
```

---

## Celery (Background Tasks)

### When to use Celery
- Sending emails
- Generating reports / exports
- Webhooks / external API calls
- Image processing / file manipulation
- Anything that takes more than ~200ms

### Task pattern
```python
# apps/notifications/tasks.py
from celery import shared_task
from django.core.mail import send_mail


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,  # retry after 60 seconds
)
def send_order_confirmation_email(self, order_id: str):
    from apps.orders.models import Order
    try:
        order = Order.objects.select_related('customer').get(id=order_id)
        send_mail(
            subject=f"Order #{order.id} confirmed",
            message=f"Dear {order.customer.first_name}, ...",
            from_email='noreply@yourdomain.com',
            recipient_list=[order.customer.email],
        )
    except Order.DoesNotExist:
        # Don't retry if order doesn't exist
        return
    except Exception as exc:
        raise self.retry(exc=exc)


# Trigger from view/service — never block the request
def place_order(order):
    # ... create order ...
    send_order_confirmation_email.delay(str(order.id))  # async!
```

---

## Database Indexing Strategy

```python
class Order(BaseModel):
    class Meta:
        indexes = [
            # Single field — for filtering
            models.Index(fields=['status']),
            models.Index(fields=['customer']),
            
            # Composite — for filtering by multiple fields
            models.Index(fields=['customer', 'status']),  # filter by both
            models.Index(fields=['status', '-created_at']),  # filter + sort
            
            # Partial index — only index active records (PostgreSQL)
            models.Index(
                fields=['customer'],
                condition=models.Q(status='pending'),
                name='pending_orders_customer_idx',
            ),
        ]
```

---

## iterator() for Large Querysets

```python
# BAD — loads 100k records into memory
for order in Order.objects.filter(status='pending'):
    process(order)

# GOOD — streams in chunks
for order in Order.objects.filter(status='pending').iterator(chunk_size=500):
    process(order)
```

---

## Rules Claude Must Follow
- ALWAYS use `select_related`/`prefetch_related` in list views — check for N+1 before finishing
- ALWAYS add indexes on fields used in `filter()`, `order_by()`, or `JOIN` conditions
- NEVER use `count()` when `exists()` is sufficient
- ALWAYS paginate list endpoints — never return unbounded lists
- ALWAYS use Celery for external API calls or tasks > 200ms
- NEVER call `.count()` or `.all()` inside a serializer `SerializerMethodField` — annotate instead
- ALWAYS use `iterator()` when processing large querysets in management commands
- ALWAYS cache expensive read-only data (category lists, config, etc.)