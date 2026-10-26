"""Estimate a fresh card from measured mean tokens and supplied RUB tariffs."""
import argparse
import csv
from decimal import Decimal, ROUND_FLOOR
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent


def positive(value):
    if value is None:return None
    result=Decimal(str(value))
    if not result.is_finite() or result<=0:raise ValueError('tariffs and token estimates must be positive')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tokens',type=Path,default=ROOT/'prompts/token_budget.csv')
    parser.add_argument('--prices',type=Path,default=ROOT/'config/pricing.example.json')
    parser.add_argument('--plan',type=Path,default=ROOT/'config/card_budget_plan.json')
    parser.add_argument('--out',type=Path)
    args=parser.parse_args()
    prices=json.loads(args.prices.read_text());plan=json.loads(args.plan.read_text())
    if prices.get('currency')!='RUB':raise ValueError('explicit RUB tariffs required')
    with args.tokens.open(newline='') as stream:measures=list(csv.DictReader(stream))
    operations=[];missing=[];total=Decimal(0)
    for operation in plan['operations']:
        pid,model=operation['prompt_id'],operation['model']
        candidates=[r for r in measures if r['prompt_id']==pid and r['model']==model]
        measure=max(candidates,key=lambda r:int(r['version'])) if candidates else {}
        rates=prices.get('models',{}).get(model,{})
        values=[positive(measure.get(k) or None) for k in ('tokens_in','tokens_out')]
        values += [positive(rates.get(k)) for k in ('input_per_1k','output_per_1k')]
        calls=Decimal(str(operation['calls_per_card']))
        if not calls.is_finite() or calls<0:raise ValueError('calls_per_card must be finite and nonnegative')
        if any(v is None for v in values) or not prices.get('source'):
            missing.append(pid+':'+model);cost=None
        else:
            tin,tout,pin,pout=values;cost=calls*(tin*pin+tout*pout)/1000;total+=cost
        operations.append(dict(**operation,estimated_rub=str(cost) if cost is not None else None))
    target=positive(plan['target_rub_per_day'])
    if target is None or target>400:raise ValueError('target must be positive and no greater than 400 RUB')
    report=dict(currency='RUB',pricing_source=prices.get('source'),target_rub_per_day=str(target),
        cost_card_rub=str(total) if not missing else None,
        cards_per_day=int((target/total).to_integral_value(rounding=ROUND_FLOOR)) if not missing and total else None,
        operations=operations,missing=missing,
        scope='Estimate from mean uncached tokens and explicit call counts; retries, ingestion and variability need separate allowance. Not a spend guarantee.')
    text=json.dumps(report,ensure_ascii=False,indent=2)+'\n'
    if args.out:
        args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(text)
    else:print(text,end='')


if __name__=='__main__':main()
