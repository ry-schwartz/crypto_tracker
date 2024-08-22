from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name = 'home'),
    path('coin/', views.coin_view, name = 'coin_view')
]