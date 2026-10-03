from django.urls import path
from . import views
app_name = "seances"
urlpatterns = [
    path('',views.planning, name='planning'),
    path('create_session/', views.create_session, name='create_session'),
    path("<uuid:session_id>/pre/", views.pre_session_page, name="pre_session_page"),
    path("<uuid:session_id>/post/", views.post_session_page, name="post_session_page"),
    path("<uuid:session_id>/cancel/", views.cancel_session, name="cancel_session"),
    path('sessions/search/', views.search_sessions, name='search_sessions'),

]
