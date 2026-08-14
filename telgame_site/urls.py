from django.contrib import admin
from django.shortcuts import redirect
from django.urls import include, path

urlpatterns = [
    # / goes to the task-shaped panel, not the raw table editor — /admin/ is
    # still there for the cases the panel deliberately doesn't cover
    path("", lambda request: redirect("panel:dashboard")),
    path("panel/", include("panel.urls")),
    path("admin/", admin.site.urls),
]
