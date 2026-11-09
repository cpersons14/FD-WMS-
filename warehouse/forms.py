from django import forms
from .models import Order, Stock
from .units import UNIT_CHOICES

class OrderForm(forms.ModelForm):
    class Meta:
        model=Order
        fields=['store','recipient','address','city','state','postal_code','reference','notes','priority','requested_ship_date','allow_backorder']
        widgets={'notes':forms.Textarea(attrs={'rows':2}),'requested_ship_date':forms.DateInput(attrs={'type':'date'})}
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['allow_backorder'].initial=True
    def clean_state(self):
        value=self.cleaned_data['state'].strip().upper()
        states='AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC PR GU VI AS MP'.split()
        if value not in states: raise forms.ValidationError('Enter a valid two-letter state or territory code.')
        return value

class StockForm(forms.Form):
    key=forms.UUIDField(widget=forms.HiddenInput)
    unit=forms.ChoiceField(choices=UNIT_CHOICES+[('BASE','Recorded base units (not yet verified)')],initial='EA')
    loose_eaches=forms.IntegerField(min_value=0,required=False,label='Additional loose eaches',help_text='Use with cases or pallets only.')
    mode=forms.ChoiceField(choices=[('count','Set counted on hand')])
    amount=forms.DecimalField(max_digits=16,decimal_places=3,min_value=0)
    note=forms.CharField(max_length=500,label='Reason / receiving reference')

class TransferForm(forms.Form):
    key=forms.UUIDField(widget=forms.HiddenInput)
    unit=forms.ChoiceField(choices=UNIT_CHOICES+[('BASE','Recorded base units (not yet verified)')],initial='EA')
    loose_eaches=forms.IntegerField(min_value=0,required=False,label='Additional loose eaches')
    target=forms.ModelChoiceField(queryset=Stock.objects.none())
    amount=forms.DecimalField(max_digits=16,decimal_places=3,min_value=0.001)
    note=forms.CharField(max_length=500,label='Transfer reason')
    def __init__(self,*args,stock,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['target'].queryset=Stock.objects.filter(part=stock.part).exclude(pk=stock.pk).select_related('location','part')

from .models import Part

class PartForm(forms.ModelForm):
    confirm_eaches=forms.BooleanField(required=False,label='I have verified that existing inventory quantities for this item are already eaches',help_text='Changing the unit label does not convert or multiply existing stock.')
    unit=forms.ChoiceField(choices=[('','Not verified'),('EA','EA — eaches')],required=False)
    class Meta:
        model=Part
        fields=['description','barcode','unit','units_per_case','cases_per_pallet','low_stock_threshold','target_stock','pick_min','count_interval_days']
    def clean_barcode(self): return self.cleaned_data['barcode'] or None
    def clean(self):
        data=super().clean()
        if self.instance.unit=='EA' and data.get('unit')!='EA':
            self.add_error('unit','The eaches base unit cannot be removed. Pack sizes can be changed without rescaling stock.')
        if data.get('unit')=='EA' and self.instance.unit!='EA' and not data.get('confirm_eaches'):
            self.add_error('confirm_eaches','Verify the existing quantity basis before confirming EA.')
        for field in ['units_per_case','cases_per_pallet']:
            if data.get(field) is not None and data[field]<1: self.add_error(field,'Use a whole number greater than zero.')
        if (data.get('units_per_case') or data.get('cases_per_pallet')) and data.get('unit')!='EA':
            self.add_error('unit','Confirm EA before configuring case/pallet conversions.')
        if data.get('cases_per_pallet') and not data.get('units_per_case'):
            self.add_error('units_per_case','Case pack is required before setting pallets.')
        if data.get('unit')=='EA' and self.instance.pk:
            if any(s.on_hand is not None and s.on_hand!=s.on_hand.to_integral_value() for s in Stock.objects.filter(part=self.instance)):
                self.add_error('unit','Some existing balances are fractional. Reconcile their unit basis before confirming eaches.')
        for field in ['low_stock_threshold','target_stock','pick_min']:
            if data.get(field) is not None and data[field]<0: self.add_error(field,'Use zero or a positive quantity.')
        if data.get('low_stock_threshold') is not None and data.get('target_stock') is not None and data['target_stock']<data['low_stock_threshold']:
            self.add_error('target_stock','Target must be at least the low-stock threshold.')
        if data.get('count_interval_days',1)<1: self.add_error('count_interval_days','Use at least one day.')
        return data

class NewBinForm(forms.Form):
    sku=forms.CharField(max_length=100,label='Part number')
    description=forms.CharField(max_length=500,required=False)
    location=forms.CharField(max_length=100,label='Location code')
    kind=forms.ChoiceField(choices=Stock._meta.get_field('kind').choices)
    opening_count=forms.DecimalField(max_digits=16,decimal_places=3,min_value=0,required=False,help_text='Blank means uncounted. Enter zero only after confirming the bin is empty.')
    held=forms.BooleanField(required=False,label='Quarantine / quality hold')
    note=forms.CharField(max_length=500,label='Opening count / hold reason')
