from django.urls import path
from . import views

urlpatterns = [
    path('', views.index,  name='index'),
    path('weather-chat/', views.weather_chat, name='weather_chat')
]