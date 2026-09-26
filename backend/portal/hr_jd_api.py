from django.db.models import F
from django.urls import path
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import hr_jd_service as service
from .hr_api import _body, _expected, _require_hr, hr_endpoint
from .hr_recruitment_api import _owned
from .hr_recruitment_models import JDVersion


def _data(jd):
    return {'id': str(jd.pk), 'request_id': str(jd.request_id), 'version': jd.version,
            'input_version': jd.input_version, 'state': jd.state, 'source': jd.source,
            'body': jd.body, 'stale': jd.stale, 'channel': jd.channel, 'custom_label': jd.custom_label,
            'source_jd_id': str(jd.source_jd_id) if jd.source_jd_id else None,
            'created_at': jd.created_at.isoformat(),
            'confirmed_at': jd.confirmed_at.isoformat() if jd.confirmed_at else None}


@api_view(['POST'])
@hr_endpoint
def generate(request, request_id):
    _require_hr(request)
    body = _body(request, {'expected_version'})
    return Response(_data(service.generate(request.user, request_id, _expected(body['expected_version']))), status=201)


@api_view(['GET', 'POST'])
@hr_endpoint
def versions(request, request_id):
    _require_hr(request)
    row = _owned(request.user, request_id)
    if request.method == 'GET':
        return Response([_data(jd) for jd in row.jd_versions.select_related('request')])
    body = _body(request, {'expected_version', 'base_jd_id', 'body'})
    jd = service.edit(request.user, request_id, _expected(body['expected_version']), body['base_jd_id'], body['body'])
    return Response(_data(jd), status=201)


@api_view(['POST'])
@hr_endpoint
def confirm(request, request_id, jd_id):
    _require_hr(request)
    body = _body(request, {'expected_version'})
    return Response(_data(service.confirm(request.user, request_id, _expected(body['expected_version']), jd_id)))


@api_view(['POST'])
@hr_endpoint
def adapt(request, request_id, jd_id):
    _require_hr(request)
    body = _body(request, {'expected_version', 'channel'}, {'custom_label'})
    jd = service.generate(request.user, request_id, _expected(body['expected_version']),
                          channel=body['channel'], source_id=jd_id, custom_label=body.get('custom_label', ''))
    return Response(_data(jd), status=201)


@api_view(['GET'])
@hr_endpoint
def confirmed(request):
    _require_hr(request)
    rows = JDVersion.objects.filter(request__created_by=request.user, state='confirmed', channel='general',
        input_version=F('request__input_version'), pk=F('request__official_jd_id')).select_related('request')
    return Response([_data(jd) for jd in rows])


urlpatterns = [
    path('requests/<uuid:request_id>/generate-jd/', generate),
    path('requests/<uuid:request_id>/jd-versions/', versions),
    path('requests/<uuid:request_id>/jd-versions/<uuid:jd_id>/confirm/', confirm),
    path('requests/<uuid:request_id>/jd-versions/<uuid:jd_id>/adapt/', adapt),
    path('confirmed-jds/', confirmed),
]
