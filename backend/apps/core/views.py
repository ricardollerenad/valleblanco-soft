# RED: todavía no hay vista de salud. Se implementa en la fase GREEN.
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthView(APIView):
    """Estado del servicio. Público, de solo lectura y sin datos sensibles."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"status": "ok"})
