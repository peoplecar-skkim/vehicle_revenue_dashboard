"""
투루카 차량별 매출 대시보드 - 데이터 생성 스크립트
실행: python generate_data.py (또는 업데이트.bat 더블클릭)

증분 업데이트:
- 연월별 실제 데이터 행 수를 비교해서 변경된 월만 재집계
- 데이터가 추가/변경되면 행 수가 달라져서 자동 감지
"""

import sqlite3, json, re, os, calendar, subprocess, sys
from datetime import datetime
from pathlib import Path

# ── 설정 ──────────────────────────────────────────────
ROUND_DB  = r"C:\Users\USER\Desktop\수경\3. 데이터\대여내역\카셰어링\rental_data_카셰어링_24~.db"
ONEWAY_DB = r"C:\Users\USER\Desktop\수경\3. 데이터\대여내역\리턴프리\rental_data_returnfree_24~.db"
OUTPUT    = Path(__file__).parent / "data.json"
# ─────────────────────────────────────────────────────

def normalize_cartype(name):
    if not name: return ''
    return re.sub(r'^\[[^\]]+\]\s*', '', str(name)).strip()

def get_row_counts(conn_r, conn_o, available):
    """연월별 실제 데이터 행 수 반환"""
    counts = {}
    cur_r = conn_r.cursor()
    cur_o = conn_o.cursor()

    for y, m in available:
        dm = calendar.monthrange(y, m)[1]
        date_from = f"{y}-{m:02d}-01"
        date_to   = f"{y}-{m:02d}-{dm:02d} 23:59:59"
        key = f"{y}-{m:02d}"

        cur_r.execute("""
            SELECT COUNT(*) FROM rentals_카셰어링
            WHERE "예약 시작일" >= ? AND "예약 시작일" <= ?
            AND (내카드주유 = 0 OR 내카드주유 IS NULL)
        """, (date_from, date_to))
        cnt_r = cur_r.fetchone()[0]

        cur_o.execute("""
            SELECT COUNT(*) FROM rentals_리턴프리
            WHERE 운행시작일 >= ? AND 운행시작일 <= ?
        """, (date_from, date_to))
        cnt_o = cur_o.fetchone()[0]

        counts[key] = f"{cnt_r}_{cnt_o}"

    return counts


def generate_weekly(conn_r, conn_o):
    """전체 연도별 주차별 집계 (왕복+편도)"""
    cur_r = conn_r.cursor()
    cur_r.execute("""
        SELECT 
            strftime('%Y', "예약 시작일") as year,
            CAST(strftime('%W', date("예약 시작일", 'weekday 0', '-6 days')) AS INTEGER) + 1 as week,
            COUNT(DISTINCT 차량번호) as vehicles,
            SUM(총청구요금) as round_rev
        FROM rentals_카셰어링
        WHERE (내카드주유 = 0 OR 내카드주유 IS NULL)
        AND "예약 시작일" IS NOT NULL
        GROUP BY year, week
    """)
    round_data = {}
    for year, week, v, rev in cur_r.fetchall():
        round_data[(year, int(week))] = {'vehicles': v, 'round_rev': (rev or 0)/1.1}

    cur_o = conn_o.cursor()
    cur_o.execute("""
        SELECT 
            strftime('%Y', 운행시작일) as year,
            CAST(strftime('%W', date(운행시작일, 'weekday 0', '-6 days')) AS INTEGER) + 1 as week,
            COUNT(DISTINCT 차량번호) as vehicles,
            SUM(총결제요금) as oneway_rev
        FROM rentals_리턴프리
        WHERE 운행시작일 IS NOT NULL
        GROUP BY year, week
    """)
    oneway_data = {}
    for year, week, v, rev in cur_o.fetchall():
        oneway_data[(year, int(week))] = {'vehicles': v, 'oneway_rev': (rev or 0)/1.1}

    all_keys = set(round_data.keys()) | set(oneway_data.keys())
    weekly = []
    for (year, week) in sorted(all_keys):
        r = round_data.get((year, week), {})
        o = oneway_data.get((year, week), {})
        weekly.append({
            'year': int(year),
            'week': week,
            'roundRev': round(r.get('round_rev', 0)),
            'onewayRev': round(o.get('oneway_rev', 0)),
            'totalRev': round(r.get('round_rev', 0) + o.get('oneway_rev', 0)),
            'vehicles': r.get('vehicles', 0)
        })
    return weekly

def generate_month(conn_r, conn_o, year, month):
    dm = calendar.monthrange(year, month)[1]
    date_from = f"{year}-{month:02d}-01"
    date_to   = f"{year}-{month:02d}-{dm:02d} 23:59:59"

    cur = conn_r.cursor()
    cur.execute("""
        SELECT 차량번호, MAX(차종명), MAX(차량유형), MAX("지역(시/도)"),
               SUM(총청구요금), COUNT(*),
               SUM((julianday("예약 종료일") - julianday("예약 시작일")) * 24)
        FROM rentals_카셰어링
        WHERE "예약 시작일" >= ? AND "예약 시작일" <= ?
          AND (내카드주유 = 0 OR 내카드주유 IS NULL)
        GROUP BY 차량번호
    """, (date_from, date_to))
    round_rows = {r[0]: r for r in cur.fetchall()}

    cur2 = conn_o.cursor()
    cur2.execute("""
        SELECT 차량번호, SUM(총결제요금), COUNT(*),
               SUM((julianday(운행종료일) - julianday(운행시작일)) * 24)
        FROM rentals_리턴프리
        WHERE 운행시작일 >= ? AND 운행시작일 <= ?
        GROUP BY 차량번호
    """, (date_from, date_to))
    oneway_rows = {r[0]: r for r in cur2.fetchall()}

    oneway_plates = set(oneway_rows.keys())
    vehicles = []
    for plate in set(round_rows.keys()) | set(oneway_rows.keys()):
        r = round_rows.get(plate)
        o = oneway_rows.get(plate)
        round_rev  = (r[4] or 0) / 1.1 if r else 0
        oneway_rev = (o[1] or 0) / 1.1 if o else 0
        op_hours   = (r[6] or 0) if r else 0
        ow_hours   = (o[3] or 0) if o else 0
        vehicles.append({
            'plate':       plate,
            'carType':     normalize_cartype(r[1] if r else '') or '미확인(편도전용)',
            'vtype':       (r[2] if r else '') or '',
            'region':      (r[3] if r else '') or '',
            'isMix':       plate in oneway_plates,
            'roundTotal':  round(round_rev),
            'onewayTotal': round(oneway_rev),
            'total':       round(round_rev + oneway_rev),
            'opHours':     round(op_hours, 2),
            'owHours':     round(ow_hours, 2),
        })
    return vehicles

def main():
    print("=" * 55)
    print("  투루카 대시보드 데이터 업데이트")
    print(f"  시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 55)

    for path, name in [(ROUND_DB, '왕복'), (ONEWAY_DB, '편도')]:
        if not os.path.exists(path):
            print(f"\n[오류] {name} DB 파일을 찾을 수 없어요:\n  {path}")
            input("\n엔터를 눌러 종료...")
            sys.exit(1)

    conn_r = sqlite3.connect(ROUND_DB)
    conn_o = sqlite3.connect(ONEWAY_DB)

    # DB에서 사용 가능한 연월 목록
    cur = conn_r.cursor()
    cur.execute("""
        SELECT DISTINCT strftime('%Y', "예약 시작일") as y,
                        strftime('%m', "예약 시작일") as m
        FROM rentals_카셰어링
        WHERE (내카드주유 = 0 OR 내카드주유 IS NULL)
        ORDER BY y, m
    """)
    available = [(int(y), int(m)) for y, m in cur.fetchall()]

    # 현재 연월별 행 수 조회
    print(f"\n연월별 행 수 확인 중...", end=' ', flush=True)
    current_counts = get_row_counts(conn_r, conn_o, available)
    print("완료")

    # 기존 data.json 로드
    existing_months = {}
    existing_counts = {}
    if OUTPUT.exists():
        try:
            with open(OUTPUT, 'r', encoding='utf-8') as f:
                saved = json.load(f)
            existing_months = saved.get('months', {})
            existing_counts = saved.get('row_counts', {})
            print(f"기존 data.json: {len(existing_months)}개월 보유")
        except:
            print("기존 data.json 읽기 실패 → 전체 재생성")

    # 집계 대상 결정
    to_generate = []
    to_skip = []
    for y, m in available:
        key = f"{y}-{m:02d}"
        cur_cnt = current_counts.get(key, '')
        prev_cnt = existing_counts.get(key, '')

        if key not in existing_months:
            to_generate.append((y, m, '🆕 신규'))
        elif cur_cnt != prev_cnt:
            to_generate.append((y, m, f'🔄 재집계 (행수 변경: {prev_cnt} → {cur_cnt})'))
        else:
            to_skip.append(key)

    print(f"\n건너뜀: {len(to_skip)}개월 (변경 없음)")
    print(f"집계 대상: {len(to_generate)}개월")
    for y, m, reason in to_generate:
        print(f"  → {y}-{m:02d} {reason}")

    if not to_generate:
        print("\n✓ 모든 데이터가 최신 상태예요. 업데이트 불필요!")
        conn_r.close(); conn_o.close()
        input("\n엔터를 눌러 종료...")
        return

    print()
    result_months = dict(existing_months)
    for i, (y, m, reason) in enumerate(to_generate, 1):
        key = f"{y}-{m:02d}"
        print(f"  [{i}/{len(to_generate)}] {key} 집계 중...", end=' ', flush=True)
        vehicles = generate_month(conn_r, conn_o, y, m)
        result_months[key] = vehicles
        print(f"{len(vehicles)}대 완료")

    # 주차별 집계
    print("\n주차별 집계 중...", end=' ', flush=True)
    weekly_data = generate_weekly(conn_r, conn_o)
    print(f"{len(weekly_data)}건 완료")

    conn_r.close()
    conn_o.close()

    # 행 수 기록 갱신
    saved_counts = dict(existing_counts)
    for y, m, _ in to_generate:
        key = f"{y}-{m:02d}"
        if key in current_counts:
            saved_counts[key] = current_counts[key]

    all_available = sorted(result_months.keys())
    output_data = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'available': all_available,
        'row_counts': saved_counts,
        'months': {k: result_months[k] for k in all_available},
        'weekly': weekly_data
    }

    print(f"\ndata.json 저장 중...")
    with open(OUTPUT, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, separators=(',', ':'))
    size_mb = OUTPUT.stat().st_size / 1024 / 1024
    print(f"저장 완료: {size_mb:.1f}MB ({len(all_available)}개월)")

    print("\nGitHub에 업로드 중...")
    repo_dir = Path(__file__).parent
    try:
        subprocess.run(['git', 'add', 'data.json'], cwd=repo_dir, check=True, capture_output=True)
        months_str = ', '.join([f"{y}-{m:02d}" for y, m, _ in to_generate])
        msg = f"data: {months_str} 업데이트"
        result = subprocess.run(['git', 'commit', '-m', msg], cwd=repo_dir, capture_output=True, text=True)
        if 'nothing to commit' in result.stdout:
            print("변경사항 없음 (Push 생략)")
        else:
            subprocess.run(['git', 'push'], cwd=repo_dir, check=True, capture_output=True)
            print("GitHub 업로드 완료! ✓")
    except subprocess.CalledProcessError as e:
        print(f"[경고] GitHub Push 실패: {e}")
        print("  → GitHub Desktop에서 직접 Push 해주세요.")

    print("\n" + "=" * 55)
    print("  완료! 대시보드에서 확인해보세요:")
    print("  https://peoplecar-skkim.github.io/vehicle_revenue_dashboard/")
    print("=" * 55)
    input("\n엔터를 눌러 종료...")

if __name__ == '__main__':
    main()