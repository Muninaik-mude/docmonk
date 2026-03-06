from django.urls import path

from .views import (
    QASessionView,
    QASessionDetailView,
    QAAskView,
    QAMessageRetryView,
    QARegenerateView,
)

urlpatterns = [
    # Session CRUD
    path("sessions",                                       QASessionView.as_view(),        name="qa-session-list-create"),
    path("sessions/<uuid:session_id>",                     QASessionDetailView.as_view(),  name="qa-session-detail"),
    # Ask (SSE streaming)
    path("sessions/<uuid:session_id>/ask",                 QAAskView.as_view(),            name="qa-ask"),
    # Message retry + regenerate (SSE streaming)
    path("messages/<uuid:message_id>/retry",               QAMessageRetryView.as_view(),   name="qa-message-retry"),
    path("messages/<uuid:message_id>/regenerate",          QARegenerateView.as_view(),     name="qa-regenerate"),
]
