"""Explicit experiment token allowance; retains organiser RUB cap and shared DB."""
import argparse
from functools import partial
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from pe import acceptance


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['matrix','cards','critics','critic-controls','attacks','judge'])
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--token-limit',type=int,required=True)
    parser.add_argument('--shard',type=int,default=0)
    parser.add_argument('--shards',type=int,default=1)
    args=parser.parse_args()
    if args.token_limit<=0:parser.error('token limit must be positive')
    args.out.mkdir(parents=True,exist_ok=True)
    config=dict(daily_token_allowance=args.token_limit,organiser_rub_cap=500,
                rub_prices='unknown',shared_db='.local/experiments/gateway.db')
    (args.out/f'runtime_{args.phase}_{args.shard}.json').write_text(json.dumps(config,indent=2)+'\n')
    acceptance.EvaluationGateway=partial(acceptance.EvaluationGateway,max_tokens=args.token_limit)
    acceptance.execute(args.phase,args.out,shard=args.shard,shards=args.shards)


if __name__=='__main__':main()
