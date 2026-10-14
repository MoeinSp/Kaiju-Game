"""The story was rewritten from 16 to 29 steps (game/story.py, 2026-10-14). story_step is an
index into STORY_QUESTS, so every player mid-story is moved to the new index right after
the last quest they had already claimed. New tutorial steps that now sit before that point
are simply skipped for them (they are past that part of the game), and nothing they already
earned is taken back."""

from django.db import migrations

# old story_step (= quests 0..step-1 claimed) -> new story_step
OLD_TO_NEW = {
    0: 0, 1: 1, 2: 2, 3: 4, 4: 5, 5: 8, 6: 13, 7: 15, 8: 16,
    9: 16, 10: 19, 11: 20, 12: 21, 13: 24, 14: 26, 15: 28, 16: 29,
}


def forward(apps, schema_editor):
    User = apps.get_model("bio_lab", "User")
    for old, new in OLD_TO_NEW.items():
        if old != new:
            User.objects.filter(story_step=old).update(story_step=-1000 - new)   # two passes: no chain re-mapping
    for new in set(OLD_TO_NEW.values()):
        User.objects.filter(story_step=-1000 - new).update(story_step=new)
    User.objects.filter(story_step__gt=16, story_step__lt=1000).exclude(story_step__in=set(OLD_TO_NEW.values())).update(story_step=29)


def backward(apps, schema_editor):
    User = apps.get_model("bio_lab", "User")
    back = {}
    for old, new in OLD_TO_NEW.items():
        back.setdefault(new, old)
    for new, old in back.items():
        if old != new:
            User.objects.filter(story_step=new).update(story_step=-2000 - old)
    for old in set(back.values()):
        User.objects.filter(story_step=-2000 - old).update(story_step=old)


class Migration(migrations.Migration):
    dependencies = [("bio_lab", "0099_bot_outbox")]
    operations = [migrations.RunPython(forward, backward)]
