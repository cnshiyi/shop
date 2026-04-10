from django.http import HttpResponse


def index(request):
    return HttpResponse('shop django 已启动')
