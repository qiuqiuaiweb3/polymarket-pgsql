import csv
import sys
from decimal import Decimal
from collections import defaultdict

# 你的目标 event 里的 4 个 market 的 YES asset id (从你的日志/DB里能查到，或者脚本自动识别)
# 这里我们让脚本自动识别：只要 outcome='YES' 就算进总和。

def analyze(csv_path):
    print(f"Analyzing {csv_path} ...")
    
    # as_of -> {asset_id -> best_ask}
    ticks_by_time = defaultdict(dict)
    
    # 统计有多少个不同的 YES asset_id，以便确认数据是否完整
    yes_assets = set()

    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # 过滤掉非 YES 或没有 ask 的行
            # 注意 CSV 里的列名是: asset_id,as_of,market_id,outcome,best_bid,best_ask,mid,source
            outcome = row.get('outcome')
            if outcome != 'YES':
                continue
            
            ask_str = row.get('best_ask')
            if not ask_str:
                continue
                
            try:
                ask = Decimal(ask_str)
            except:
                continue
                
            as_of = row['as_of']
            asset_id = row['asset_id']
            
            ticks_by_time[as_of][asset_id] = ask
            yes_assets.add(asset_id)

    print(f"Found {len(yes_assets)} unique YES assets.")
    if len(yes_assets) < 4:
        print("Warning: Less than 4 YES assets found. Maybe data is incomplete?")

    # 开始寻找套利机会
    arb_count = 0
    
    # 按时间排序遍历
    for as_of in sorted(ticks_by_time.keys()):
        prices = ticks_by_time[as_of]
        
        # 严格一点：只有当收集齐了所有已知的 YES 资产价格时才计算
        # (假设你关注的那 4 个 market 都有数据)
        if len(prices) < len(yes_assets):
            continue
            
        total_ask = sum(prices.values())
        
        if total_ask < 1.0:
            arb_count += 1
            print(f"[{as_of}] SUM_YES = {total_ask:.6f}")
            # 打印明细
            for aid, p in prices.items():
                print(f"  {aid[-6:]}: {p}", end=" ")
            print("\n")

    print(f"\nTotal opportunities found: {arb_count}")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python scripts/analyze_csv_arb.py <path_to_csv>")
        sys.exit(1)
    analyze(sys.argv[1])
