from django.conf import settings
from rest_framework import serializers


class TripQuerySerializer(serializers.Serializer):
    start = serializers.CharField(max_length=200, help_text='"City, ST", "City, State", an address, or "lat,lng".')
    finish = serializers.CharField(max_length=200, help_text='Same formats as start.')
    start_fuel = serializers.FloatField(
        required=False, default=0.0, min_value=0.0,
        help_text='Gallons in the tank at the start (default 0: the whole trip is paid for).',
    )
    stop_penalty = serializers.FloatField(
        required=False, default=None, min_value=0.0, max_value=1000.0,
        help_text='USD cost assigned to each fuel stop when optimising (default from settings; 0 = cheapest fuel only).',
    )

    def validate_start_fuel(self, value):
        capacity = settings.VEHICLE_RANGE_MILES / settings.VEHICLE_MPG
        if value > capacity:
            raise serializers.ValidationError(f'Ensure this value is less than or equal to {capacity:g}.')
        return value

    def validate_stop_penalty(self, value):
        return settings.FUEL_STOP_PENALTY_USD if value is None else value
