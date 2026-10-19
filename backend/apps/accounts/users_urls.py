from django.urls import path

from .views import InviteUserView

urlpatterns = [
    path("", InviteUserView.as_view(), name="user-invite"),
]
