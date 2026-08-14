"""URLs for the operator web panel.

Mounted under /panel/ (see telgame_site/urls.py). Django's own /admin/ stays
where it is — it's the raw table editor of last resort, while this panel is the
task-shaped one: change the palette, switch a theme, take a backup.
"""

from django.contrib.auth import views as auth_views
from django.urls import path

from panel import views

app_name = "panel"

urlpatterns = [
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="panel/login.html", redirect_authenticated_user=True
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(next_page="panel:login"), name="logout"),
    path("", views.dashboard, name="dashboard"),
    path("buttons/", views.button_styles, name="button_styles"),
    path("buttons/emoji/", views.button_emoji_view, name="button_emoji"),
    path("emoji/", views.text_emoji_view, name="text_emoji"),
    path("loadouts/", views.loadouts, name="loadouts"),
    path("loadouts/<int:loadout_id>/export/", views.loadout_export, name="loadout_export"),
    path("backups/", views.backups, name="backups"),
    path("backups/download/", views.backup_download, name="backup_download"),
    path("players/", views.players, name="players"),
    path("channels/", views.channels, name="channels"),
]
