"""Explicit unit conversions. Never infer a pack size from an inventory balance."""
from decimal import Decimal

UNIT_CHOICES=[('EA','Eaches'),('CASE','Cases'),('PALLET','Pallets')]


def pack_values(source):
    # Order lines carry immutable pack snapshots. Live stock uses the current item master.
    if hasattr(source,'base_unit'):
        return source.base_unit,source.case_pack_snapshot,source.pallet_cases_snapshot
    return source.unit,source.units_per_case,source.cases_per_pallet


def convert(source,amount,unit='BASE',loose_eaches=0,allow_zero=False):
    from .services import quantity,WorkflowError
    unit=(unit or 'BASE').upper()
    if unit not in {'EA','CASE','PALLET','BASE'}: raise WorkflowError('Choose eaches, cases or pallets.')
    base,case_pack,pallet_cases=pack_values(source)
    if unit!='BASE' and base!='EA': raise WorkflowError('Confirm this item’s recorded inventory is in eaches (EA) before using each/case/pallet entry.')
    loose=quantity(loose_eaches or 0,allow_zero=True)
    if loose and unit not in {'CASE','PALLET'}: raise WorkflowError('Loose eaches can only be added to case or pallet entries.')
    q=quantity(amount,allow_zero=allow_zero or loose>0)
    if (base=='EA' or unit!='BASE') and (q!=q.to_integral_value() or loose!=loose.to_integral_value()):
        raise WorkflowError('Enter whole eaches, cases and pallets. Use loose eaches for a partial pack.')
    factor=1
    if unit in {'CASE','PALLET'}:
        if not case_pack: raise WorkflowError('Set the confirmed units per case for this item first.')
        factor=case_pack
    if unit=='PALLET':
        if not pallet_cases: raise WorkflowError('Set the confirmed cases per pallet for this item first. Use cases/eaches for a nonstandard pallet.')
        factor*=pallet_cases
    total=quantity(q*factor+loose,allow_zero=allow_zero)
    entry=dict(quantity=str(q),unit=base or 'UNVERIFIED' if unit=='BASE' else unit,factor=str(factor),loose_eaches=str(loose),base_quantity=str(total),base_unit=base or 'UNVERIFIED')
    return total,entry


def breakdown(source,amount):
    if amount is None: return 'Uncounted'
    base,case_pack,pallet_cases=pack_values(source)
    q=Decimal(str(amount))
    if base!='EA': return f'{q:g} {base or "unverified units"}'
    if not case_pack or q!=q.to_integral_value(): return f'{q:g} EA'
    cases,loose=divmod(int(q),case_pack)
    return f'{cases} case equivalents + {loose} EA'
