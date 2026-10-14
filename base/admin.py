from django.contrib import admin
from django.contrib.admin.views.main import ChangeList


PAGE_SIZE_CHOICES = (25, 50, 100)


class PageSizeChangeList(ChangeList):
    def get_filters_params(self, params=None):
        filter_params = super().get_filters_params(params)
        filter_params.pop('per_page', None)
        return filter_params

    def get_results(self, request):
        requested_page_size = request.GET.get('per_page')
        if requested_page_size in {str(size) for size in PAGE_SIZE_CHOICES}:
            self.list_per_page = int(requested_page_size)
        super().get_results(request)


class PaginatedModelAdmin(admin.ModelAdmin):
    list_per_page = PAGE_SIZE_CHOICES[0]
    change_list_template = 'admin/change_list_page_size.html'

    def get_changelist(self, request, **kwargs):
        return PageSizeChangeList

    def changelist_view(self, request, extra_context=None):
        page_size_query_params = [
            (key, value)
            for key, values in request.GET.lists()
            if key not in {'all', 'p', 'per_page'}
            for value in values
        ]
        context = {
            **(extra_context or {}),
            'page_size_query_params': page_size_query_params,
        }
        return super().changelist_view(request, extra_context=context)
