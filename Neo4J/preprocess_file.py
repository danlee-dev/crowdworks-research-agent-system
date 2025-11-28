import json
import os
import glob

# === 설정 ===
folder_path = "../elasticsearch/preprocessed_datas"  # JSON들이 있는 폴더
output_folder = "./report_data"                      # 결과 저장 폴더
os.makedirs(output_folder, exist_ok=True)

def normalize_name(name: str) -> str:
    # 파일명에서 '凸'를 모두 제거하고 좌우 공백을 정리.
    return name.replace("凸", "").strip()

existing_txt_files = glob.glob(os.path.join(output_folder, "*.txt"))

existing_names_normalized = {
    os.path.splitext(os.path.basename(p))[0]
    for p in existing_txt_files
}

# --- 처리할 JSON 파일 목록 (정렬) ---
json_files = sorted(glob.glob(os.path.join(folder_path, "*.json")))

processed_count = 0
skipped_exists = 0
skipped_errors = 0

for json_file_path in json_files:
    base_name_original = os.path.splitext(os.path.basename(json_file_path))[0]
    base_name_normalized = normalize_name(base_name_original)

    # 이미 동일한(정규화된) 이름의 결과가 있으면 스킵
    if base_name_normalized in existing_names_normalized:
        print(f"⏭️ 스킵(이미 존재): {base_name_normalized}.txt")
        skipped_exists += 1
        continue

    # JSON 로드
    try:
        with open(json_file_path, "r", encoding="utf-8") as f:
            data = json.load(f)  # 기대형태: 리스트
    except json.JSONDecodeError:
        print(f"⚠️ JSON 형식 오류: {json_file_path}")
        skipped_errors += 1
        continue
    except Exception as e:
        print(f"⚠️ 파일 열기/읽기 오류: {json_file_path} -> {e}")
        skipped_errors += 1
        continue

    if not isinstance(data, list):
        print(f"⚠️ 예상과 다른 JSON 구조(리스트 아님): {json_file_path}")
        skipped_errors += 1
        continue

    # 페이지 콘텐츠 처리 (첫 항목은 제목 포함, 이후는 제목 제거)
    page_contents_processed = []
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            # 비정상 항목은 건너뜀
            continue
        content = item.get("page_content", "")
        if not isinstance(content, str):
            content = str(content)
        content = content.strip()

        if idx == 0:
            page_contents_processed.append(content)
        else:
            if "\n\n" in content:
                content = content.split("\n\n", 1)[1].strip()
            page_contents_processed.append(content)

    file_text = "\n\n".join(page_contents_processed)

    # 출력 파일 경로: '정규화된' 베이스네임을 사용
    safe_base = base_name_normalized or base_name_original  # 극단적 케이스 대비
    output_path = os.path.join(output_folder, safe_base + ".txt")

    # 저장
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(file_text)
        print(f"✅ 저장 완료: {output_path}")
        processed_count += 1
        # 같은 실행 중 중복 방지 위해 즉시 집합에 추가
        existing_names_normalized.add(base_name_normalized)
    except Exception as e:
        print(f"⚠️ 저장 오류: {output_path} -> {e}")
        skipped_errors += 1

print(
    f"총 {len(json_files)}개의 JSON 파일 중 "
    f"{processed_count}개 처리, {skipped_exists}개 스킵(이미 존재), {skipped_errors}개 스킵(오류)"
)

