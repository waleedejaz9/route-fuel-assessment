from django.urls import path

from . import views

urlpatterns = [
    path('route/', views.TripPlanView.as_view(), name='route-plan'),
    path('route/map/', views.trip_map_view, name='route-map'),
]
