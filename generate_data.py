"""
투루카 차량별 매출 대시보드 - 데이터 생성 스크립트
실행: python generate_data.py (또는 업데이트.bat 더블클릭)

증분 업데이트:
- 왕복/편도 DB의 적재이력을 비교해서 변경된 월만 재집계
- 새 데이터를 DB에 넣고 실행하면 해당 월만 자동으로 업데이트됨
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

def extract_ym(filepath):
    """파일경로에서 연월 추출 (예: 26.07.xlsx → 2026-07)"""
    m = re.search(r'(\d{2})\.(\d{2})\.xlsx', filepath)
    if m: return f"20{m.group(1)}-{m.group(2)}"
    m = re.search(r'(\d{2})년\s*(\d{1,2})월', filepath)
    if m: return f"20{m.group(1)}-{int(m.group(2)):02d}"
    m = re.search(r'(\d{4})[-_](\d{2})', filepath)
    if m: return f"{m.group(1)}-{m.group(2)}"
    return None

def get_load_history(db_path):
    """DB의 적재이력에서 연월별 최신 적재일시 반환"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT 파일경로, 적재일시 FROM 적재이력")
    rows = cur.fetchall()
    conn.close()

    ym_map = {}
    for path, ts in rows:
        ym = extract_ym(path or '')
        if ym:
            if ym not in ym_map or ts > ym_map[ym]:
                ym_map[ym] = ts
    return ym_map

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

    # DB 파일 존재 확인
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

    # 현재 적재이력 (연월별 최신 적재일시)
    history_r = get_load_history(ROUND_DB)
    history_o = get_load_history(ONEWAY_DB)
    # 둘 중 더 최신 시각을 해당 월의 기준으로
    current_history = {}
    for ym in set(history_r) | set(history_o):
        ts_r = history_r.get(ym, '')
        ts_o = history_o.get(ym, '')
        current_history[ym] = max(ts_r, ts_o)

    # 기존 data.json 로드
    existing_months = {}
    existing_history = {}
    if OUTPUT.exists():
        try:
            with open(OUTPUT, 'r', encoding='utf-8') as f:
                saved = json.load(f)
            existing_months = saved.get('months', {})
            existing_history = saved.get('load_history', {})
            print(f"\n기존 data.json: {len(existing_months)}개월 보유")
        except:
            print("\n기존 data.json 읽기 실패 → 전체 재생성")

    # 집계 대상 결정
    to_generate = []
    to_skip = []
    for y, m in available:
        key = f"{y}-{m:02d}"
        cur_ts = current_history.get(key, '')
        prev_ts = existing_history.get(key, '')

        if key not in existing_months:
            to_generate.append((y, m, '🆕 신규'))
        elif cur_ts != prev_ts:
            to_generate.append((y, m, f'🔄 재집계 (적재이력 변경: {cur_ts[:16]})'))
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

    # 집계 실행
    print()
    result_months = dict(existing_months)
    for i, (y, m, reason) in enumerate(to_generate, 1):
        key = f"{y}-{m:02d}"
        print(f"  [{i}/{len(to_generate)}] {key} 집계 중...", end=' ', flush=True)
        vehicles = generate_month(conn_r, conn_o, y, m)
        result_months[key] = vehicles
        print(f"{len(vehicles)}대 완료")

    conn_r.close()
    conn_o.close()

    # data.json 저장 (적재이력도 함께 저장)
    all_available = sorted(result_months.keys())
    # 저장 시 현재 적재이력 갱신 (집계한 월만)
    saved_history = dict(existing_history)
    for y, m, _ in to_generate:
        key = f"{y}-{m:02d}"
        if key in current_history:
            saved_history[key] = current_history[key]

    output_data = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'available': all_available,
        'load_history': saved_history,
        'months': {k: result_months[k] for k in all_available}
    }

    print(f"\ndata.json 저장 중...")
    with open(OUTPUT, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, separators=(',', ':'))
    size_mb = OUTPUT.stat().st_size / 1024 / 1024
    print(f"저장 완료: {size_mb:.1f}MB ({len(all_available)}개월)")

    # GitHub Push
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