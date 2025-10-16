import os
import re
import json

def extract_subchapters(root_path, folder_name):
    folder_dir = os.path.join(root_path, folder_name)

    results = []
    chapter_indices = {}
    subchapter_indices = {}

    for filename in sorted(os.listdir(folder_dir)):
        file_path = os.path.join(folder_dir, filename)
        if not os.path.isfile(file_path):
            continue
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        chapter = None
        subchapter = None
        content_accum = []
        started = False

        for i, line in enumerate(lines):
            stripped = line.strip()
            if not started:
                m = re.match(r'^# (\d+\.)\s*(.*)', stripped)
                if m:
                    chapter = m.group(2)
                    started = True
                    if chapter not in chapter_indices:
                        chapter_indices[chapter] = len(chapter_indices)
                    if chapter not in subchapter_indices:
                        subchapter_indices[chapter] = 0
                continue
            if stripped.startswith('## '):
                # Save previous subchapter if exists
                if subchapter is not None:
                    results.append({
                        'pdf': folder_name,
                        'chapter': chapter,
                        'chapter_index': chapter_indices[chapter],
                        'subchapter': subchapter,
                        'subchapter_index': subchapter_indices[chapter] - 1,
                        'content': ''.join(content_accum).strip()
                    })
                subchapter = stripped[3:]
                content_accum = []
                subchapter_indices[chapter] += 1
            else:
                if subchapter is not None:
                    content_accum.append(line)
        # Save last subchapter
        if subchapter is not None:
            results.append({
                'pdf': folder_name,
                'chapter': chapter,
                'chapter_index': chapter_indices[chapter],
                'subchapter': subchapter,
                'subchapter_index': subchapter_indices[chapter] - 1,
                'content': ''.join(content_accum).strip()
            })
    return results

if __name__ == '__main__':
    with open ("output.json", "w") as f:
        dci = extract_subchapters("nccn_markdowns", "Ductal carcinoma insitu")
        inf = extract_subchapters("nccn_markdowns", "Inflammatory Breast Cancer")
        json.dump(dci + inf, f)