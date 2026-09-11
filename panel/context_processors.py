from bio_lab.models import ButtonEmojiOverride, EmojiOverride
from game import button_emoji, emoji

def panel_nav_stats(request):
    if not request.path.startswith('/panel/'):
        return {}
    try:
        txt_total = len(emoji.EMOJI_DEFS)
        txt_set = EmojiOverride.objects.exclude(key__startswith=emoji._GLYPH_PREFIX).count()
        btn_total = len(button_emoji.BUTTON_EMOJI_DEFS)
        btn_set = ButtonEmojiOverride.objects.count()
        return {
            'nav_text_emoji_stat': f'{txt_set}/{txt_total}',
            'nav_button_emoji_stat': f'{btn_set}/{btn_total}',
        }
    except Exception:
        return {}
