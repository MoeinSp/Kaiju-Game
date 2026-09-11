"""Small public API for advertisers.

One endpoint: "has this Telegram user started the bot?" — an advertiser buying a
campaign hands over a user id or @username and gets back a plain true/false, so
they can verify the traffic they sent actually landed in the bot. Protected by a
shared secret (config.AD_API_KEY); no session/login, since the caller is a
third party, not a panel operator.

"Started" == the user's `started_gate` flag is set (turned on by /start). It's a
resettable flag rather than mere row existence, so the whole population can be forced
to /start again (bulk-reset the flag) without deleting anyone's data.
"""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from bio_lab.models import User
from config import AD_API_KEY


@csrf_exempt
@require_GET
def user_started(request):
    key = request.GET.get("key") or request.headers.get("X-API-Key", "")
    if not AD_API_KEY or key != AD_API_KEY:
        return JsonResponse({"error": "unauthorized"}, status=403)

    ident = (request.GET.get("user") or "").strip().lstrip("@")
    if not ident:
        return JsonResponse({"error": "missing 'user' parameter"}, status=400)

    if ident.isdigit():
        user = User.objects.filter(id=int(ident)).first()
    else:
        user = User.objects.filter(username__iexact=ident).first()

    if user is None or not user.started_gate:
        return JsonResponse({"started": False})
    return JsonResponse(
        {
            "started": True,
            "id": user.id,
            "username": user.username,
            "joined": user.created_at.date().isoformat(),
        }
    )
