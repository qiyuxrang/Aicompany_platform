"""Conservative project identities, independent of the immutable notice history."""
import hashlib
import re
import unicodedata

from django.db import transaction

from .tender_models import TenderOpportunity, TenderOpportunityUserState


def _text(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value or '')).casefold()


def project_discriminator(title):
    title = _text(title)
    lots = re.findall(r'(?:第?[一二三四五六七八九十百\d]{1,8}(?:标段|标包|包段|包)(?!含|括|装)|[a-z](?:标段|标包)|(?:标段|标包|包号)[a-z一二三四五六七八九十\d]+)', title)
    rounds = re.findall(r'(?:第?[二三四五六七八九十\d]+次(?:招标|采购)?|重新招标|重新采购|再次招标)', title)
    return ','.join(sorted(set(lots + rounds)))


def project_group_key(opportunity):
    code, purchaser = _text(opportunity.project_code), _text(opportunity.purchaser)
    # Missing/placeholder codes and prose never establish cross-site identity.
    if (len(code) < 5 or len(code) > 120 or not re.search(r'\d', code)
            or code in {'00000', '12345'} or len(purchaser) < 5
            or re.search(r'[。；;：:]|详见|不适用|暂无|未公布', code + purchaser)):
        return opportunity.opportunity_key
    # Retain explicit lot identity. No fuzzy title matching or enterprise aliases.
    identity = '|'.join([code, purchaser, project_discriminator(opportunity.project_name)])
    return 'project:v1:' + hashlib.sha256(identity.encode()).hexdigest()


def group_key(opportunity):
    return opportunity.project_group_key or opportunity.opportunity_key


@transaction.atomic
def assign_project_group(opportunity):
    previous = group_key(opportunity)
    current = project_group_key(opportunity)
    if previous != current:
        for state in TenderOpportunityUserState.objects.filter(project_group_key=previous):
            destination, created = TenderOpportunityUserState.objects.get_or_create(
                user_id=state.user_id, project_group_key=current,
                defaults={name: getattr(state, name) for name in ('is_read', 'is_favorite', 'is_irrelevant')})
            if not created:
                destination.is_read |= state.is_read
                destination.is_favorite |= state.is_favorite
                # Do not hide a group because only one of two formerly separate items was dismissed.
                destination.is_irrelevant &= state.is_irrelevant
                destination.save()
    opportunity.project_group_key = current
    return current


def group_members(opportunity):
    key = group_key(opportunity)
    return TenderOpportunity.objects.filter(project_group_key=key) if opportunity.project_group_key else TenderOpportunity.objects.filter(pk=opportunity.pk)
