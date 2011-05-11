# -*- coding: UTF-8 -*-
from django import forms
from django import http
from django import template
from django.conf.urls.defaults import url, patterns
from django.contrib import admin
from django.core import urlresolvers
from django.core.cache import cache
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render_to_response
from assopy import models
from assopy.clients import genro

class CountryAdmin(admin.ModelAdmin):
    list_display = ('printable_name', 'vat_company', 'vat_company_verify', 'vat_person')
    list_editable = ('vat_company', 'vat_company_verify', 'vat_person')
    search_fields = ('name', 'printable_name', 'iso', 'numcode')

admin.site.register(models.Country, CountryAdmin)

class OrderItemAdminForm(forms.ModelForm):
    class Meta:
        model = models.OrderItem

    def __init__(self, *args, **kwargs):
        super(OrderItemAdminForm, self).__init__(*args, **kwargs)
        from conference.models import Ticket
        self.fields['ticket'].queryset = Ticket.objects.all().select_related('fare')

class OrderItemInlineAdmin(admin.TabularInline):
    model = models.OrderItem
    form = OrderItemAdminForm

class OrderAdminForm(forms.ModelForm):
    class Meta:
        model = models.Order

    def __init__(self, *args, **kwargs):
        super(OrderAdminForm, self).__init__(*args, **kwargs)
        self.fields['user'].queryset = models.User.objects.all().select_related('user')

class OrderAdmin(admin.ModelAdmin):
    list_display = ('code', '_user', '_created', 'method', '_items', '_complete', '_invoice', '_total_nodiscount', '_discount', '_total_payed',)
    list_select_related = True
    list_filter = ('method',)
    list_per_page = 20
    search_fields = ('code', 'user__user__first_name', 'user__user__last_name', 'user__user__email', 'billing_notes')
    date_hierarchy = 'created'
    actions = ('do_edit_invoices',)

    form = OrderAdminForm

    inlines = (
        OrderItemInlineAdmin,
    )

    def _user(self, o):
        url = urlresolvers.reverse('admin:assopy_user_change', args=(o.user.id,))
        return '<a href="%s">%s</a>' % (url, o.user.name())
    _user.short_description = 'buyer'
    _user.allow_tags = True

    def _items(self, o):
        return o.orderitem_set.exclude(ticket=None).count()
    _items.short_description = '#Tickets'

    def _created(self, o):
        return o.created.strftime('%d %b %Y - %H:%M:%S')

    def _total_nodiscount(self, o):
        return o.total(apply_discounts=False)
    _total_nodiscount.short_description = 'Total'

    def _discount(self, o):
        return o.total(apply_discounts=False) - o.total()
    _discount.short_description = 'Discount'

    def _total_payed(self, o):
        return o.total()
    _total_payed.short_description = 'Payed'

    def _invoice(self, o):
        output = []
        for i in o.invoices.all():
            output.append('<a href="%s">%s%s</a>' % (genro.invoice_url(i.assopy_id), i.code, ' *' if not i.payment_date else ''))
        return ' '.join(output)
    _invoice.allow_tags = True

    def get_urls(self):
        urls = super(OrderAdmin, self).get_urls()
        my_urls = patterns('',
            url(r'^invoices/$', self.admin_site.admin_view(self.edit_invoices), name='assopy-edit-invoices'),
            url(r'^stats/$', self.admin_site.admin_view(self.stats), name='assopy-order-stats'),
        )
        return my_urls + urls

    def do_edit_invoices(self, request, queryset):
        ids = [ str(o.id) for o in queryset if not o.complete() ]
        if ids:
            url = urlresolvers.reverse('admin:assopy-edit-invoices') + '?id=' + ','.join(ids)
            return redirect(url)
        else:
            self.message_user(request, 'no orders')
    do_edit_invoices.short_description = 'Edit/Make invoices'

    def edit_invoices(self, request):
        try:
            ids = map(int, request.GET['id'].split(','))
        except KeyError:
            return http.HttpResponseBadRequest('orders id missing')
        except ValueError:
            return http.HttpResponseBadRequest('invalid id list')
        orders = models.Order.objects.filter(id__in=ids)
        if not orders.count():
            return redirect('admin:assopy_order_changelist')
            
        class FormPaymentDate(forms.Form):
            date = forms.DateField(input_formats=('%Y/%m/%d',), help_text='Enter the date (YYYY/MM/DD) of receipt of payment. Leave blank to issue an invoice without a payment', required=False)

        if request.method == 'POST':
            form = FormPaymentDate(data=request.POST)
            if form.is_valid():
                d = form.cleaned_data['date']
                for o in orders:
                    genro.confirm_order(o.assopy_id, o.total(), d)
                    o.complete()
                return redirect('admin:assopy_order_changelist')
        else:
            form = FormPaymentDate()
        ctx = {
            'orders': orders,
            'form': form,
            'ids': request.GET.get('id'),
        }
        return render_to_response('assopy/admin/edit_invoices.html', ctx, context_instance=template.RequestContext(request))

    def stats(self, request):
        from conference.models import Ticket
        from django.db.models import Sum, Count
        orders = models.Order.objects.filter(_complete=True)
        tickets = Ticket.objects.filter(orderitem__order__in=orders)
        order_items_details = models.OrderItem.objects\
            .values('ticket__fare__code', 'ticket__fare__name')\
            .annotate(total=Sum('price'), count=Count('pk'))\
            .order_by('-total')
        ctx = {
            'orders': orders,
            'tickets': tickets,
            'order_items_details': order_items_details,
        }
        return render_to_response('assopy/admin/order_stats.html', ctx, context_instance=template.RequestContext(request))

admin.site.register(models.Order, OrderAdmin)

class CouponAdminForm(forms.ModelForm):
    class Meta:
        model = models.Coupon

    def __init__(self, *args, **kwargs):
        super(CouponAdminForm, self).__init__(*args, **kwargs)
        self.fields['user'].queryset = models.User.objects.all().select_related('user')

    def clean_code(self):
        return self.cleaned_data['code'].upper()

class CouponAdmin(admin.ModelAdmin):
    list_display = ('code', 'value', 'start_validity', 'end_validity', 'max_usage')
    form = CouponAdminForm

admin.site.register(models.Coupon, CouponAdmin)

class UserOAuthInfoAdmin(admin.TabularInline):
    model = models.UserOAuthInfo

class UserAdmin(admin.ModelAdmin):
    list_display = ('_name', '_email', 'phone', 'address', '_identities', '_login')
    list_select_related = True
    search_fields = ('user__first_name', 'user__last_name', 'user__email', 'address',)

    inlines = (
        UserOAuthInfoAdmin,
    )

    def _name(self, o):
        return o.name()
    _name.short_description = 'name'
    _name.admin_order_field = 'user__first_name'

    def _email(self, o):
        return o.user.email
    _email.short_description = 'email'
    _email.admin_order_field = 'user__email'

    def _login(self, o):
        url = urlresolvers.reverse('admin:assopy-login-user', kwargs={'uid': o.id})
        return '<a href="%s">use this user</a>' % (url,)
    _login.short_description = 'login as this user'
    _login.allow_tags = True

    def _identities(self, o):
        return ','.join(i['provider'] for i in o.identities.values('provider'))
    _identities.short_description = '#id'

    def get_urls(self):
        urls = super(UserAdmin, self).get_urls()
        my_urls = patterns('',
            url(r'^(?P<uid>\d+)/login/$', self.admin_site.admin_view(self.login_as_user), name='assopy-login-user'),
            url(r'^(?P<uid>\d+)/order/$', self.admin_site.admin_view(self.new_order), name='assopy-user-order'),
            url(r'^resurrect/$', self.resurrect_user, name='assopy-resurrect-user'),
        )
        return my_urls + urls

    def login_as_user(self, request, uid):
        udata = (request.user.id, '%s %s' % (request.user.first_name, request.user.last_name),)
        user = get_object_or_404(models.User, pk=uid)
        from django.contrib import auth 
        auth.logout(request)
        user = auth.authenticate(uid=user.user.id)
        auth.login(request, user)
        request.session['resurrect_user'] = udata
        return http.HttpResponseRedirect('/')

    def resurrect_user(self, request):
        uid = request.session['resurrect_user'][0]
        from django.contrib import auth
        auth.logout(request)
        user = auth.authenticate(uid=uid)
        if user.is_superuser:
            auth.login(request, user)
        return http.HttpResponseRedirect('/')

    @transaction.commit_on_success
    def new_order(self, request, uid):
        from assopy import forms as aforms
        from conference.models import Fare
        from conference.settings import CONFERENCE

        user = get_object_or_404(models.User, pk=uid)

        class FormTickets(aforms.FormTickets):
            coupon = forms.CharField(label='Coupon(s)', required=False)
            country = forms.CharField(max_length=2, required=False)
            address = forms.CharField(max_length=150, required=False)
            billing_notes = forms.CharField(required=False)
            remote = forms.BooleanField(required=False, initial=True, help_text='debug only, fill the order on the remote backend')
            def __init__(self, *args, **kwargs):
                super(FormTickets, self).__init__(*args, **kwargs)
                self.fields['payment'].choices = (('admin', 'Admin'),) + tuple(self.fields['payment'].choices)
                self.fields['payment'].initial = 'admin'
            def available_fares(self):
                return Fare.objects.available(conference=CONFERENCE)

            def clean_country(self):
                data = self.cleaned_data.get('country')
                if data:
                    try:
                        data = models.Country.objects.get(pk=data)
                    except models.Country.DoesNotExist:
                        raise forms.ValidationError('Invalid country: %s' % data)
                return data

            def clean_coupon(self):
                data = self.cleaned_data.get('coupon')
                output = []
                if data:
                    for c in data.split(' '):
                        try:
                            output.append(models.Coupon.objects.get(conference=CONFERENCE, code=c))
                        except models.Coupon.DoesNotExist:
                            raise forms.ValidationError('invalid coupon "%s"' % c)
                return output

        if request.method == 'POST':
            form = FormTickets(data=request.POST)
            if form.is_valid():
                data = form.cleaned_data
                models.Order.objects.create(
                    user=user,
                    payment=data['payment'], 
                    items=data['tickets'],
                    billing_notes=data['billing_notes'],
                    coupons=data['coupon'],
                    remote=data['remote'],
                    country=data['country'],
                    address=data['address'],
                )
                return redirect('admin:assopy_user_change', user.id,)
        else:
            form = FormTickets()
        ctx = {
            'user': user,
            'form': form,
        }
        return render_to_response('admin/assopy/user/new_order.html', ctx, context_instance=template.RequestContext(request))
admin.site.register(models.User, UserAdmin)
