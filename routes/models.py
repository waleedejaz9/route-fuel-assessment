from django.db import models


class FuelStation(models.Model):
    """A truck stop from the fuel prices file, geocoded to its city's coordinates."""

    class GeocodeSource(models.TextChoices):
        GAZETTEER = 'gazetteer', 'US Census Gazetteer'
        ORS = 'ors', 'OpenRouteService geocoder'

    opis_id = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    rack_id = models.PositiveIntegerField(null=True, blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=8, help_text='Retail price, USD per gallon')
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    geocode_source = models.CharField(max_length=16, choices=GeocodeSource.choices, blank=True)

    class Meta:
        ordering = ['state', 'city', 'name']
        indexes = [models.Index(fields=['state', 'city'])]

    def __str__(self):
        return f'{self.name} ({self.city}, {self.state}) ${self.price:.3f}'
