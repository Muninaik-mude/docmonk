from django.urls import path

from .views import QASessionView, QASessionDetailView, QAAskView, QAInteractionRetryView

urlpatterns = [
    # Session CRUD
    path('sessions',                                    QASessionView.as_view(),           name='qa-session-list-create'),
    path('sessions/<uuid:session_id>',                  QASessionDetailView.as_view(),     name='qa-session-detail'),
    # Ask
    path('sessions/<uuid:session_id>/ask',              QAAskView.as_view(),               name='qa-ask'),
    # Retry a failed interaction
    path('interactions/<uuid:interaction_id>/retry',    QAInteractionRetryView.as_view(),  name='qa-interaction-retry'),
]
