---
name: django-models
description: >
  Apply this skill when designing Django models, writing ORM queries,
  creating migrations, or optimizing database access patterns. Enforces
  production-grade data modeling habits.
---

# Django Models & ORM

## Core Philosophy
- **Model the domain, not the database** — think in entities and relationships
- **Query intentionally** — every ORM call has a cost, make it visible
- **Never trust N+1** — always audit queries with `django-debug-toolbar` or logging
- **Migrations are code** — review them like you review logic, not auto-generated noise

---

## Model Design Patterns

### Standard Model
```python
from django.db import models
from django.contrib.auth import get_user_model
from apps.core.models import BaseModel  # Always use your base

User = get_user_model()


class Product(BaseModel):
    """
    Represents a product in the catalog.
    
    Relationships:
    - belongs to one Category
    - has many OrderItems
    - created by one User
    """
    name = models.CharField(max_length=255, db_index=True)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    stock = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True)
    
    category = models.ForeignKey(
        'catalog.Category',
        on_delete=models.PROTECT,  # Never CASCADE on important data
        related_name='products',
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_products',
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['category', 'is_active']),  # Composite index for common filter
            models.Index(fields=['slug']),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(price__gte=0),
                name='product_price_non_negative',
            ),
        ]

    def __str__(self):
        return f"{self.name} (${self.price})"

    def is_in_stock(self) -> bool:
        return self.stock > 0
```

---

## Custom Manager Pattern

```python
class ProductQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def in_stock(self):
        return self.filter(stock__gt=0)

    def by_category(self, category_slug):
        return self.filter(category__slug=category_slug)

    def with_category(self):
        return self.select_related('category')


class ProductManager(models.Manager):
    def get_queryset(self):
        return ProductQuerySet(self.model, using=self._db)

    def active(self):
        return self.get_queryset().active()


# In model:
class Product(BaseModel):
    objects = ProductManager()
    # Usage: Product.objects.active().in_stock().with_category()
```

---

## ORM Query Patterns

### Always optimize upfront
```python
# BAD — triggers N+1 queries
orders = Order.objects.all()
for order in orders:
    print(order.customer.email)  # query per row!

# GOOD — single query with JOIN
orders = Order.objects.select_related('customer').all()

# BAD — N+1 for reverse relations
orders = Order.objects.all()
for order in orders:
    print(order.items.all())  # query per order!

# GOOD — prefetch in bulk
orders = Order.objects.prefetch_related('items').all()

# GOOD — prefetch with filtering
from django.db.models import Prefetch
orders = Order.objects.prefetch_related(
    Prefetch('items', queryset=OrderItem.objects.select_related('product'))
)
```

### Aggregation
```python
from django.db.models import Sum, Count, Avg, Q

# Total revenue per category
from django.db.models import F
Product.objects.values('category__name').annotate(
    product_count=Count('id'),
    avg_price=Avg('price'),
    total_stock_value=Sum(F('price') * F('stock')),
)

# Conditional aggregation
Order.objects.aggregate(
    total=Count('id'),
    completed=Count('id', filter=Q(status='completed')),
    cancelled=Count('id', filter=Q(status='cancelled')),
)
```

### Bulk operations — never loop to save
```python
# BAD — 1000 queries
for item in data:
    Product.objects.create(**item)

# GOOD — 1 query
Product.objects.bulk_create([Product(**item) for item in data], batch_size=500)

# GOOD — bulk update specific fields
Product.objects.filter(category=category).update(is_active=False)

# GOOD — bulk_update when different values per row
products = list(Product.objects.filter(id__in=ids))
for p in products:
    p.price = calculate_new_price(p)
Product.objects.bulk_update(products, ['price'], batch_size=500)
```

### Only fetch what you need
```python
# Fetch only required fields
Product.objects.only('id', 'name', 'price').filter(is_active=True)

# Exclude heavy fields
Product.objects.defer('description', 'metadata').all()

# Values for read-only data (returns dicts, no model overhead)
Product.objects.values('id', 'name', 'price').filter(is_active=True)

# values_list for flat lists
active_ids = Product.objects.filter(is_active=True).values_list('id', flat=True)
```

---

## Migration Best Practices

```python
# Always name your migrations descriptively
# python manage.py makemigrations --name add_stock_index_to_product

# For adding columns to large tables — make nullable first, backfill, then add constraint
# Migration 1: Add nullable
class Migration(migrations.Migration):
    operations = [
        migrations.AddField(
            model_name='product',
            name='sku',
            field=models.CharField(max_length=50, null=True),
        ),
    ]

# Migration 2: Data migration (backfill)
def generate_skus(apps, schema_editor):
    Product = apps.get_model('catalog', 'Product')
    for product in Product.objects.iterator(chunk_size=500):
        product.sku = f"PRD-{product.id.hex[:8].upper()}"
        product.save(update_fields=['sku'])

class Migration(migrations.Migration):
    operations = [
        migrations.RunPython(generate_skus, migrations.RunPython.noop),
    ]

# Migration 3: Add constraint
class Migration(migrations.Migration):
    operations = [
        migrations.AlterField(
            model_name='product',
            name='sku',
            field=models.CharField(max_length=50, unique=True),
        ),
    ]
```

---

## Custom User Model (Always set up at project start)

```python
# apps/users/models.py
from django.contrib.auth.models import AbstractUser
from django.db import models
from apps.core.models import TimeStampedModel


class User(AbstractUser, TimeStampedModel):
    """
    Custom user model — ALWAYS use this, never Django's built-in.
    Set AUTH_USER_MODEL = 'users.User' in settings BEFORE first migration.
    """
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=20, blank=True)
    avatar = models.URLField(blank=True)
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    class Meta:
        db_table = 'users'
```

---

## Rules Claude Must Follow
- ALWAYS use `get_user_model()`, never import `User` directly
- ALWAYS define `__str__` on every model
- ALWAYS use `on_delete=models.PROTECT` on critical FK relationships — never silent CASCADE
- NEVER use `.all()` without a filter in production code paths
- ALWAYS use `select_related` for ForeignKey, `prefetch_related` for M2M/reverse FK
- NEVER use `save()` in a loop — use `bulk_create` or `bulk_update`
- ALWAYS use `update_fields=['field1', 'field2']` when saving partial updates
- ALWAYS define custom managers/querysets for reusable filter logic
- ALWAYS set `AUTH_USER_MODEL` before the first migration — changing it later is painful