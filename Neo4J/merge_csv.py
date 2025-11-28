import os
import glob
import pandas as pd

# 경로 설정
csv_folder = "./extracted_graph"     # 생성된 개별 CSV들이 있는 폴더
txt_folder = "./report_data"      # 원문 TXT들이 있는 폴더 (제목=첫 줄)
output_path = "./import/report.csv"

# 합칠 CSV 목록
csv_files = sorted(glob.glob(os.path.join(csv_folder, "*.csv")))

# 제목 추출 함수: 같은 이름의 TXT에서 첫 줄, 없으면 파일명 사용
def get_title(base_name: str) -> str:
    txt_path = os.path.join(txt_folder, base_name + ".txt")
    if os.path.exists(txt_path):
        with open(txt_path, "r", encoding="utf-8") as f:
            first_line = f.readline().strip()
            return first_line if first_line else base_name
    return base_name

dfs = []
for file in csv_files:
    base = os.path.splitext(os.path.basename(file))[0]
    title = get_title(base)
    try:
        df = pd.read_csv(
            file,
            dtype=str,
            engine="python",       # 형식 불량 행이 있어도 처리 유연
            on_bad_lines="skip"    # 필드 수 안 맞는 행은 스킵
        )

        # '문서제목' 컬럼 추가 (맨 앞에 삽입)
        if "문서제목" in df.columns:
            df.drop(columns=["문서제목"], inplace=True)
        df.insert(0, "문서제목", title)

        # 머지를 위해 필요한 핵심 컬럼이 없으면 만들어 둠(빈 값) → 이후 필터에서 제거됨
        key_cols = ["엔터티1","엔터티1유형","관계","엔터티2","엔터티2유형"]
        for col in key_cols:
            if col not in df.columns:
                df[col] = ""

        dfs.append(df)
    except Exception as e:
        print(f"⚠️ {file} 읽기 오류: {e}")

if not dfs:
    raise SystemExit("❌ 읽어올 CSV가 없습니다.")

# 1) 이어붙이기
merged_df = pd.concat(dfs, ignore_index=True)

# 2) 핵심 5컬럼 비어있는 행 제거 (NaN/공백 포함)
key_cols = ["엔터티1","엔터티1유형","관계","엔터티2","엔터티2유형"]
merged_df.dropna(subset=key_cols, inplace=True)
for col in key_cols:
    merged_df = merged_df[merged_df[col].astype(str).str.strip() != ""]

# 3) 저장
merged_df.to_csv(output_path, index=False, encoding="utf-8-sig")
print(f"✅ 총 {len(csv_files)}개 파일을 제목 포함 머지하여 저장했습니다 → {output_path}")
