from django.apps import AppConfig


class PropertiesConfig(AppConfig):
    name = "apps.properties"
    verbose_name = "propiedades"

    def ready(self):
        from apps.core.profile import register_profile_provider

        from .profile import profile_provider

        register_profile_provider(profile_provider)
