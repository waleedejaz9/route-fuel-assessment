import logging
from urllib.parse import urlencode

from django.shortcuts import render
from django.urls import reverse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import TripQuerySerializer
from .services.optimizer import FuelPlanError
from .services.ors_client import ORSError, ORSRateLimitError, RouteNotFoundError
from .services.trip_planner import TripInputError, plan_trip

logger = logging.getLogger(__name__)


class TripPlanError(Exception):
    def __init__(self, payload, status_code):
        super().__init__(payload)
        self.payload = payload
        self.status_code = status_code


def run_trip_plan(params):
    """Validate params and plan the trip; raise TripPlanError with an HTTP-ready payload."""
    serializer = TripQuerySerializer(data=params)
    if not serializer.is_valid():
        raise TripPlanError(serializer.errors, status.HTTP_400_BAD_REQUEST)
    query = serializer.validated_data
    try:
        return query, plan_trip(query['start'], query['finish'], query['start_fuel'], query['stop_penalty'])
    except TripInputError as exc:
        raise TripPlanError(exc.errors, status.HTTP_400_BAD_REQUEST) from exc
    except (RouteNotFoundError, FuelPlanError) as exc:
        raise TripPlanError({'detail': str(exc)}, status.HTTP_422_UNPROCESSABLE_ENTITY) from exc
    except ORSRateLimitError as exc:
        raise TripPlanError({'detail': 'Routing service rate limit reached; try again shortly.'},
                            status.HTTP_503_SERVICE_UNAVAILABLE) from exc
    except ORSError as exc:
        logger.exception('Routing service failure')
        raise TripPlanError({'detail': f'Routing service error: {exc}'}, status.HTTP_502_BAD_GATEWAY) from exc


class TripPlanView(APIView):
    """Route between two US locations with the cheapest fuel stops.

    GET /api/v1/route/?start=Chicago, IL&finish=Denver, CO[&start_fuel=0]
    POST /api/v1/route/ with the same fields as JSON.
    """

    def get(self, request):
        return self._respond(request, request.query_params)

    def post(self, request):
        return self._respond(request, request.data)

    def _respond(self, request, params):
        try:
            query, plan = run_trip_plan(params)
        except TripPlanError as exc:
            return Response(exc.payload, status=exc.status_code)
        map_query = urlencode({key: query[key] for key in ('start', 'finish', 'start_fuel', 'stop_penalty')})
        plan['map_url'] = request.build_absolute_uri(f"{reverse('route-map')}?{map_query}")
        return Response(plan)


def trip_map_view(request):
    """Interactive Leaflet map of the same plan (served from cache after the API call)."""
    try:
        _, plan = run_trip_plan(request.GET)
    except TripPlanError as exc:
        errors = [(field, ' '.join(map(str, msgs)) if isinstance(msgs, list) else str(msgs))
                  for field, msgs in exc.payload.items()]
        return render(request, 'routes/map.html', {'errors': errors}, status=exc.status_code)
    return render(request, 'routes/map.html', {'plan': plan})
