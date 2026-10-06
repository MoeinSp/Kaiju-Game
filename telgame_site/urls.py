from django.contrib import admin
from django.shortcuts import redirect
from django.urls import include, path

from telgame_site import miniapp_views
from telgame_site.api import user_started

urlpatterns = [
    # / goes to the task-shaped panel, not the raw table editor — /admin/ is
    # still there for the cases the panel deliberately doesn't cover
    path("", lambda request: redirect("panel:dashboard")),
    path("panel/", include("panel.urls")),
    path("admin/", admin.site.urls),
    # public advertiser API: GET /api/started/?key=...&user=<id or @username>
    path("api/started/", user_started, name="api_user_started"),
    # Mini App — step 1: can players open one at all? (telgame_site/miniapp_views.py)
    path("app/tg.js", miniapp_views.sdk_js),
    path("app/test/", miniapp_views.test_page),
    path("app/test/img/", miniapp_views.test_image),
    path("app/test/report/", miniapp_views.test_report),
    # Mini App (telgame_site/mini/): shell, static files, images and the discovered API modules
    path("app/", include("telgame_site.mini.urls")),
]
