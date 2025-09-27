import fitz  # PyMuPDF
import os
import re
from typing import List, Dict, Tuple, Optional
import logging

TOLERANCE = 5

class PDFChapterExtractor:
    def __init__(self, pdf_path: str, output_dir: str = "output"):
        """
        Initialize the PDF Chapter Extractor
        
        Args:
            pdf_path: Path to the PDF file
            output_dir: Directory to save markdown files and images
        """
        self.pdf_path = pdf_path
        self.output_dir = output_dir
        self.doc = None
        
        # Create output directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(os.path.join(output_dir, "images"), exist_ok=True)
        
        # Configure logging
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)
    
    def open_pdf(self):
        """Open the PDF document"""
        try:
            self.doc = fitz.open(self.pdf_path)
            self.logger.info(f"Successfully opened PDF with {len(self.doc)} pages")
        except Exception as e:
            self.logger.error(f"Error opening PDF: {e}")
            raise
    
    def close_pdf(self):
        """Close the PDF document"""
        if self.doc:
            self.doc.close()
    
    def get_chapter_pages(self, first_page: int, last_page: int) -> List[int]:
        """Get list of page numbers for a chapter based on explicit page range"""
        if not self.doc:
            self.open_pdf()
        
        # Convert to 0-indexed and validate
        first_page_idx = first_page - 1
        last_page_idx = last_page - 1
        
        if first_page_idx < 0:
            raise ValueError("First page must be >= 1")
        if last_page_idx >= len(self.doc):
            raise ValueError(f"Last page {last_page} exceeds document length ({len(self.doc)} pages)")
        if first_page_idx > last_page_idx:
            raise ValueError("First page must be <= last page")
        
        chapter_pages = list(range(first_page_idx, last_page_idx + 1))
        self.logger.info(f"Processing pages {first_page} to {last_page} ({len(chapter_pages)} pages)")
        
        return chapter_pages
    
    def should_exclude_text(self, text: str, bbox, page_height: float) -> bool:
        """Check if text should be excluded (headers, footers, promotional content)"""
        text_clean = text.strip()
        
        if len(text_clean) < 5:
            return True
        
        # Page headers (top 15% of page)
        if bbox[1] < page_height * 0.15:
            header_patterns = [
                r'\d+\s+About\s+.*?».*?',
                r'Chapter\s+\d+',
                r'NCCN Guidelines',
                r'\d+\s+\w+.*?».*?',
            ]
            for pattern in header_patterns:
                if re.search(pattern, text_clean, re.IGNORECASE):
                    return True
        
        # Page footers (bottom 10% of page)  
        if bbox[3] > page_height * 0.9:
            footer_patterns = [
                r'NCCN Guidelines for Patients',
                r'Invasive Breast Cancer, \d+',
                r'^\d+$',
            ]
            for pattern in footer_patterns:
                if re.search(pattern, text_clean, re.IGNORECASE):
                    return True
        
        return False
    
    def analyze_text_styles(self, pages):
        """
        Analyze text styles across pages to detect main body font and layout.
        """
        all_blocks = []
        all_font_sizes = []
        all_positions = []

        for page_num in pages:
            page = self.doc[page_num]
            text_dict = page.get_text("dict")
            page_height = page.rect.height

            for block_idx, block in enumerate(text_dict["blocks"]):
                if "lines" not in block:
                    continue  # skip image/vector blocks
                bbox = block['bbox']

                block_text = ""
                font_sizes = []
                colors = []
                span_details = []

                for line in block["lines"]:
                    for span in line["spans"]:
                        block_text += span["text"]
                        font_sizes.append(span["size"])
                        colors.append(span["color"])
                        span_details.append(span)

                if not block_text.strip():
                    continue

                # Exclusion check
                if self.should_exclude_text(block_text, bbox, page_height):
                    continue

                avg_font_size = sum(font_sizes) / len(font_sizes) if font_sizes else 0
                dominant_color = max(set(colors), key=colors.count) if colors else 0

                block_info = {
                    "text": block_text.strip(),
                    "bbox": bbox,
                    "font_size": avg_font_size,
                    "color": dominant_color,
                    "x_left": bbox[0],
                    "x_right": bbox[2],
                    "y_center": (bbox[1] + bbox[3]) / 2,
                    "width": bbox[2] - bbox[0],
                    "page": page_num,
                }

                all_blocks.append(block_info)
                for i in range(len(font_sizes)):
                    all_font_sizes.append(font_sizes[i])
                    all_positions.append(bbox[0])

        return {
            "all_blocks": all_blocks,
            "all_font_sizes": all_font_sizes,
            "all_positions": all_positions,
        }
    
    def detect_main_content_area(self, style_analysis: Dict) -> Dict[str, float]:
        """
        Detect the main content area based on the most common text positions and styles
        """
        all_blocks = style_analysis['all_blocks']
        
        if not all_blocks:
            # Fallback
            page_width = self.doc[0].rect.width if self.doc else 600
            return {
                'left_col_left': 50,
                'left_col_right': page_width / 2 - 10,
                'right_col_left': page_width / 2 + 10,
                'right_col_right': page_width - 50
            }
        
        # Find the most common font size (likely main content)
        font_sizes = style_analysis['all_font_sizes']
        print("FONT SIZES:")
        print(font_sizes)
        main_font_size = max(set(font_sizes), key=font_sizes.count)
        print("MAIN FONT SIZE:")
        print(main_font_size)
        
        # Filter to blocks with main font size (these are likely main content)
        main_content_blocks = [
            block for block in all_blocks 
            if abs(block['font_size'] - main_font_size) < 1  # Allow small variance
        ]
        
        if not main_content_blocks:
            main_content_blocks = all_blocks
        
        # Analyze x positions of main content blocks
        left_positions = [block['x_left'] for block in main_content_blocks]
        right_positions = [block['x_right'] for block in main_content_blocks]
        
        # Group similar positions
        left_groups = self.group_positions(left_positions, tolerance=20)
        right_groups = self.group_positions(right_positions, tolerance=20)
        
        # Find most common left positions (column starts)
        left_groups.sort(key=len, reverse=True)
        
        # Get the two most common column start positions
        if len(left_groups) >= 2:
            left_col_left = min(left_groups[0])
            right_col_left = min(left_groups[1])
            
            # Make sure left comes before right
            if left_col_left > right_col_left:
                left_col_left, right_col_left = right_col_left, left_col_left
        else:
            # Single column or fallback
            left_col_left = min(left_positions) if left_positions else 50
            page_width = max(right_positions) if right_positions else 600
            right_col_left = page_width * 0.55
        
        # Find corresponding right boundaries
        left_col_candidates = [pos for pos in right_positions if pos < right_col_left + 50]
        right_col_candidates = [pos for pos in right_positions if pos > right_col_left - 50]
        
        left_col_right = max(left_col_candidates) if left_col_candidates else right_col_left - 10
        right_col_right = max(right_col_candidates) if right_col_candidates else max(right_positions)
        
        boundaries = {
            'left_col_left': left_col_left,
            'left_col_right': left_col_right,
            'right_col_left': right_col_left,
            'right_col_right': right_col_right,
            'main_font_size': main_font_size
        }
        
        self.logger.info(f"Detected main content boundaries: {boundaries}")
        return boundaries
    
    def group_positions(self, positions: List[float], tolerance: float) -> List[List[float]]:
        """Group similar positions together"""
        if not positions:
            return []
        
        positions = sorted(positions)
        groups = []
        current_group = [positions[0]]
        
        for pos in positions[1:]:
            if pos - current_group[-1] <= tolerance:
                current_group.append(pos)
            else:
                groups.append(current_group)
                current_group = [pos]
        
        groups.append(current_group)
        return groups
    
    def classify_text_blocks(self, page, page_num, main_font_size, boundaries):
        """
        Classify text blocks into main content and other elements.
        """
        main_blocks = []
        other_blocks = []

        text_dict = page.get_text("dict")
        page_height = page.rect.height

        for block_idx, block in enumerate(text_dict["blocks"]):
            if "lines" not in block:
                continue
            bbox = block['bbox']

            block_text = ""
            font_sizes = []
            colors = []
            span_details = []
            for line in block["lines"]:
                for span in line["spans"]:
                    block_text += span["text"]
                    font_sizes.append(span["size"])
                    colors.append(span["color"])
                    span_details.append(span)

            if not block_text.strip():
                continue

            if self.should_exclude_text(block_text, bbox, page_height):
                continue

            avg_font_size = sum(font_sizes) / len(font_sizes) if font_sizes else 0

            # Column + font checks
            fits_left_col = abs(bbox[0] - boundaries["left_col_left"]) < TOLERANCE
            fits_right_col = abs(bbox[0] - boundaries["right_col_left"]) < TOLERANCE
            font_match = abs(avg_font_size - main_font_size) < 2

            # Only print classification result for key blocks
            if block_text.strip().startswith("What is invasive breast cancer"):
                print(f"\n=== IMPORTANT BLOCK: {block_text.strip()[:30]}... ===")
                print(f"  Fits Left Col? {fits_left_col}")
                print(f"  Fits Right Col? {fits_right_col}")
                print(f"  Font Match? {font_match} (block:{avg_font_size:.1f} vs main:{main_font_size:.1f})")

            if (fits_left_col or fits_right_col) and font_match:
                main_blocks.append({
                    "text": block_text.strip(),
                    "bbox": bbox,
                    "font_size": avg_font_size,
                    "x_left": bbox[0],
                    "x_right": bbox[2],
                    "y_top": bbox[1],
                    "y_center": (bbox[1] + bbox[3]) / 2,
                    "page": page_num,
                    "column": "left" if fits_left_col else "right"
                })
            else:
                other_blocks.append({
                    "text": block_text.strip(),
                    "bbox": bbox,
                    "font_size": avg_font_size,
                    "x_left": bbox[0],
                    "x_right": bbox[2],
                    "y_top": bbox[1],
                    "y_center": (bbox[1] + bbox[3]) / 2,
                    "page": page_num
                })

        return main_blocks, other_blocks
    
    def sort_main_blocks(self, blocks: List[Dict]) -> List[Dict]:
        """Sort main column blocks in reading order"""
        left_blocks = [b for b in blocks if b['column'] == 'left']
        right_blocks = [b for b in blocks if b['column'] == 'right']
        
        left_blocks.sort(key=lambda b: b['y_top'])
        right_blocks.sort(key=lambda b: b['y_top'])
        
        return left_blocks + right_blocks
    
    def is_heading(self, text: str, font_size: float, main_font_size: float) -> bool:
        """Detect headings based on text and style"""
        text = text.strip()
        
        # Question headings
        if text.endswith('?') and 10 < len(text) < 100:
            return True
        
        # Larger font size suggests heading
        if font_size > main_font_size + 1:
            return True
        
        # Title case without period, short
        if (len(text) < 80 and 
            not text.endswith('.') and
            text[0].isupper() and
            len(text.split()) <= 10):
            return True
        
        return False
    
    def extract_images_from_page(self, page, page_num: int) -> List[Dict]:
        """Extract images from a page"""
        images = []
        image_list = page.get_images()
        
        for img_index, img in enumerate(image_list):
            try:
                xref = img[0]
                pix = fitz.Pixmap(self.doc, xref)
                
                # Skip small images
                if pix.width < 100 or pix.height < 100:
                    pix = None
                    continue
                
                # Convert to PNG
                if pix.n < 5:  # GRAY or RGB
                    img_data = pix.tobytes("png")
                else:  # CMYK, convert to RGB
                    pix1 = fitz.Pixmap(fitz.csRGB, pix)
                    img_data = pix1.tobytes("png")
                    pix1 = None
                
                # Save image
                img_filename = f"page{page_num + 1}_img{img_index + 1}.png"
                img_path = os.path.join(self.output_dir, "images", img_filename)
                
                with open(img_path, "wb") as img_file:
                    img_file.write(img_data)
                
                # Get image position
                img_rect = page.get_image_rects(xref)[0] if page.get_image_rects(xref) else None
                
                images.append({
                    'filename': img_filename,
                    'rect': img_rect,
                    'y_center': (img_rect[1] + img_rect[3]) / 2 if img_rect else 0,
                    'inserted': False
                })
                
                pix = None
                self.logger.info(f"Extracted image: {img_filename}")
                
            except Exception as e:
                self.logger.error(f"Error extracting image {img_index} from page {page_num}: {e}")
                continue
        
        return images
    
    def associate_other_blocks_with_images(self, other_blocks: List[Dict], images: List[Dict]) -> List[Dict]:
        """Associate other blocks with nearby images"""
        for other_block in other_blocks:
            other_y = other_block['y_center']
            
            # Find closest image
            best_img = None
            min_distance = float('inf')
            
            for img in images:
                if not img['rect']:
                    continue
                
                img_y = img['y_center']
                distance = abs(other_y - img_y)
                
                if distance < min_distance:
                    min_distance = distance
                    best_img = img
            
            # Associate if reasonably close
            if best_img and min_distance < 200:
                if 'captions' not in best_img:
                    best_img['captions'] = []
                best_img['captions'].append(other_block['text'])
        
        return images
    
    def place_images(self, main_blocks: List[Dict], images: List[Dict]) -> List[Dict]:
        """Determine where to place images in the main content flow"""
        for img in images:
            if not img['rect']:
                continue
            
            img_y = img['y_center']
            
            # Find the best position - after a block that comes before the image
            best_block_idx = None
            min_distance = float('inf')
            
            for i, block in enumerate(main_blocks):
                block_y = block['y_center']
                
                # Only consider blocks that come before the image
                if block_y <= img_y:
                    distance = img_y - block_y
                    if distance < min_distance:
                        min_distance = distance
                        best_block_idx = i
            
            if best_block_idx is not None:
                img['insert_after_block'] = best_block_idx
        
        return images
    
    def generate_main_content_markdown(self, main_blocks, chapter_title, main_font_size):
        """
        Generate markdown for main content blocks.
        """
        markdown = [f"# {chapter_title} - Main Content\n"]

        for idx, block in enumerate(main_blocks):
            text = block["text"]
            if not text:
                continue

            if self.is_heading(text, block["font_size"], main_font_size):
                markdown.append(f"## {text.strip()}\n")
            else:
                markdown.append(f"{text.strip()}\n")

        return "\n".join(markdown)
    
    def generate_other_elements_markdown(self, other_blocks, images, chapter_title):
        """
        Generate markdown for 'other' blocks and images, but skip items that have already
        been included in `combined` (they will have ob['used']=True or img['inserted']=True).
        """
        markdown = [f"# {chapter_title} - Other Elements\n"]

        # Print other_blocks that were NOT used in combined
        for idx, block in enumerate(other_blocks):
            if block.get('used'):
                print(f"[OTHER MD] Skipping block {idx} (already used in combined): {block['text'][:60]!r}")
                continue
            text = block.get("text", "").strip()
            if text:
                markdown.append(f"> {re.sub(r'\\s+', ' ', text)}\n")
                print(f"[OTHER MD] Block {idx}: Saved as QUOTE -> {text[:50]!r}")

        # Print images that were not inserted in combined
        for idx, img in enumerate(images):
            if img.get('inserted'):
                print(f"[OTHER MD] Skipping image {img['filename']} (already inserted in combined)")
                continue
            markdown.append(f"![Image {idx}]({img['filename']})\n")
            print(f"[OTHER MD] Image {idx}: Saved -> {img['filename']}")
            if "captions" in img:
                for cap in img["captions"]:
                    markdown.append(f"> {re.sub(r'\\s+', ' ', cap.strip())}\n")
                    print(f"[OTHER MD] Image {idx}: Caption -> {cap[:50]!r}")

        return "\n".join(markdown)

    
    def generate_combined_markdown(
    self,
    main_blocks: List[Dict],
    other_blocks: List[Dict],
    images: List[Dict],
    title: str,
    main_font_size: float,
    boundaries: Dict[str, float],
    ) -> str:
        """
        Generate combined markdown:
        - beginning-section other_blocks go first (plain paragraphs, top-left)
        - main_blocks (with inserted other-heading candidates that are in-column)
        - image-related other_blocks and images go at the end of the page
        This function marks other_blocks used by setting ob['used']=True and marks images
        inserted inline by setting img['inserted']=True to avoid duplicates.
        """
        def normalize_text(s: str) -> str:
            return re.sub(r'\s+', ' ', (s or "").strip()).lower()

        content = f"# {title}\n\n"

        # Group other_blocks and main_blocks by page
        other_by_page = {}
        for ob in other_blocks:
            other_by_page.setdefault(ob["page"], []).append(ob)

        main_by_page = {}
        for mb in main_blocks:
            main_by_page.setdefault(mb["page"], []).append(mb)

        pages = sorted(set(list(main_by_page.keys()) + list(other_by_page.keys())))

        # Build a set of normalized captions already attached to images (to avoid duplication)
        caption_norms = set()
        for img in images:
            for c in img.get("captions", []) or []:
                caption_norms.add(normalize_text(c))

        # Helper: whether block center is in a main column
        def _in_column(block, boundaries, tol=30):
            center_x = (block.get("x_left", 0) + block.get("x_right", 0)) / 2.0
            left_ok = (boundaries["left_col_left"] - tol) <= center_x <= (boundaries["left_col_right"] + tol)
            right_ok = (boundaries["right_col_left"] - tol) <= center_x <= (boundaries["right_col_right"] + tol)
            return left_ok or right_ok

        for page in pages:
            page_main = main_by_page.get(page, [])
            page_other = other_by_page.get(page, [])[:]  # copy

            page_height = self.doc[page].rect.height if self.doc else 1000
            # classification buckets
            beginning_section = []
            heading_candidates = []
            image_related = []

            # Partition other blocks into beginning_section vs rest
            for ob in page_other:
                y_top = ob.get("y_top", ob.get("y_center", 0))
                x_left = ob.get("x_left", 0)
                in_col = _in_column(ob, boundaries)

                # Candidate: top-left "beginning section"
                if y_top < page_height * 0.25 and x_left <= (boundaries.get("left_col_right", 300) + 40):
                    beginning_section.append(ob)
                    print(f"[COMBINED-DECIDE] BEGINNING_SECTION candidate (page {page+1}) preview: {ob['text'][:80]!r}")
                else:
                    # Candidate headings: must be in a main column AND look like headings,
                    # and be short (avoid long paragraph fragments becoming headings)
                    is_heading_style = self.is_heading(ob["text"], ob["font_size"], main_font_size)
                    short_enough = len(ob["text"].split()) <= 12
                    if in_col and is_heading_style and short_enough:
                        heading_candidates.append(ob)
                        print(f"[COMBINED-DECIDE] HEADING_CANDIDATE (page {page+1}) preview: {ob['text'][:80]!r} (in_col={in_col}, short={short_enough})")
                    else:
                        image_related.append(ob)
                        print(f"[COMBINED-DECIDE] IMAGE_RELATED (page {page+1}) preview: {ob['text'][:80]!r}")

            # 1) Write beginning section (top-left) first, as plain paragraphs (never headered)
            beginning_section.sort(key=lambda b: b.get("y_top", b.get("y_center", 0)))
            for ob in beginning_section:
                normalized = normalize_text(ob.get("text", ""))
                if not normalized:
                    continue
                # Avoid duplicates if caption already attached to an image
                if normalized in caption_norms:
                    print(f"[COMBINED] Skipping beginning-section block because its text appears as an image caption: {ob['text'][:60]!r}")
                    ob['used'] = True
                    continue
                content += f"{re.sub(r'\\s+', ' ', ob['text'].strip())}\n\n"
                ob['used'] = True
                print(f"[COMBINED] Beginning-section (page {page+1}): {ob['text'][:80]!r}")

            # 2) Merge heading_candidates into the main flow (preserve main order)
            merged = list(page_main)  # shallow copy preserves order
            # sort heading_candidates top-to-bottom
            heading_candidates.sort(key=lambda b: b.get("y_top", b.get("y_center", 0)))
            for ob in heading_candidates:
                ob_y = ob.get("y_top", ob.get("y_center", 0))
                inserted = False
                for idx_mb, mb in enumerate(merged):
                    mb_y = mb.get("y_top", mb.get("y_center", 0))
                    if ob_y < mb_y:
                        merged.insert(idx_mb, ob)
                        ob['used'] = True
                        print(f"[COMBINED-INSERT] Inserted other-heading into page {page+1} at pos {idx_mb}: {ob['text'][:80]!r}")
                        inserted = True
                        break
                if not inserted:
                    merged.append(ob)
                    ob['used'] = True
                    print(f"[COMBINED-APPEND] Appended other-heading to end of page {page+1}: {ob['text'][:80]!r}")

            # 3) Render merged list
            for item in merged:
                is_main = item in page_main
                text = re.sub(r'\s+', ' ', item.get("text", "").strip())
                if not text:
                    continue

                if is_main:
                    # main blocks: header decision allowed (but still require column membership for header)
                    if self.is_heading(text, item["font_size"], main_font_size) and _in_column(item, boundaries):
                        content += f"\n## {text}\n\n"
                        print(f"[COMBINED] Main heading (page {page+1}) GLOBAL_IDX={item.get('global_index')} -> {text[:80]!r}")
                    else:
                        content += f"{text}\n\n"
                        print(f"[COMBINED] Main paragraph (page {page+1}) GLOBAL_IDX={item.get('global_index')} -> {text[:80]!r}")

                    # Insert images attached to this main block (by global index)
                    gidx = item.get("global_index")
                    if gidx is not None:
                        for img in images:
                            if img.get("insert_after_block") == gidx and not img.get("inserted"):
                                img_md = f"![Image](images/{img['filename']})"
                                for cap in img.get("captions", []) or []:
                                    img_md += f"\n*{re.sub(r'\\s+', ' ', cap.strip())}*"
                                content += f"\n{img_md}\n\n"
                                img["inserted"] = True
                                print(f"[COMBINED] Inserted image after main GLOBAL_IDX={gidx}: {img['filename']}")
                else:
                    # this is an inserted other-heading (we only inserted validated heading_candidates here)
                    content += f"\n## {text}\n\n"
                    print(f"[COMBINED] Inserted other-heading into flow (page {page+1}): {text[:80]!r}")
                    # item was already marked used when inserted

            # 4) Append image-related text at the end of the page (as captions/aside)
            # Avoid printing any text that already appears in image captions (caption_norms) or that was marked used.
            remaining_image_related = []
            for ob in image_related:
                if ob.get('used'):
                    continue
                normalized = normalize_text(ob.get("text", ""))
                if normalized in caption_norms:
                    # skip because the caption will be printed with the image
                    ob['used'] = True
                    print(f"[COMBINED] Skipping image-related text because it matches an image caption (page {page+1}): {ob['text'][:80]!r}")
                    continue
                remaining_image_related.append(ob)

            if remaining_image_related:
                remaining_image_related.sort(key=lambda b: b.get("y_top", b.get("y_center", 0)))
                print(f"[COMBINED] Appending {len(remaining_image_related)} image-related blocks at end of page {page+1}")
                for ob in remaining_image_related:
                    text = re.sub(r'\s+', ' ', ob.get("text", "").strip())
                    if text:
                        content += f"*{text}*\n\n"
                        ob['used'] = True
                        print(f"[COMBINED] Appended image-related (page {page+1}): {text[:80]!r}")

            # 5) Add any remaining images for this page that weren't inserted inline
            for img in images:
                if img.get('inserted'):
                    continue
                # determine if image belongs to this page by filename convention or rect page check
                # (we rely on earlier extraction which uses page indexes)
                # to avoid complexity, check if the filename includes the page number (common in saved naming)
                if f"page{page+1}_" in img.get('filename', '') or img.get('rect') and img.get('rect')[1] < page_height + 1:
                    img_md = f"![Image](images/{img['filename']})"
                    for cap in img.get("captions", []) or []:
                        img_md += f"\n*{re.sub(r'\\s+', ' ', cap.strip())}*"
                    content += f"\n{img_md}\n\n"
                    img['inserted'] = True
                    print(f"[COMBINED] Appended remaining image for page {page+1}: {img['filename']}")

        return content




    
    def extract_chapter_to_markdown(self, first_page: int, last_page: int, chapter_title: str = None) -> Dict[str, str]:
        """Extract a specific page range and convert to markdown"""
        if not self.doc:
            self.open_pdf()
        
        chapter_pages = self.get_chapter_pages(first_page, last_page)
        
        if not chapter_pages:
            empty_content = f"# Chapter\n\nNo content available for pages {first_page}-{last_page}."
            return {
                'main': empty_content,
                'other': empty_content,
                'combined': empty_content
            }
        
        # Analyze text styles to understand layout
        style_analysis = self.analyze_text_styles(chapter_pages)
        
        # Detect main content area
        main_content_area = self.detect_main_content_area(style_analysis)
        
        title = chapter_title if chapter_title else f"Chapter (Pages {first_page}-{last_page})"
        
        all_main_blocks = []
        all_other_blocks = []
        all_images = []
        
        # Process each page
        for page_num in chapter_pages:
            page = self.doc[page_num]
            
            # Classify text blocks
            main_blocks, other_blocks = self.classify_text_blocks(page, page_num, main_content_area['main_font_size'], main_content_area)
            
            # Extract images
            page_images = self.extract_images_from_page(page, page_num)
            all_images.extend(page_images)
            
            # Associate other blocks with images
            all_images = self.associate_other_blocks_with_images(other_blocks, all_images)
            
            # Collect blocks
            all_main_blocks.extend(main_blocks)
            all_other_blocks.extend(other_blocks)
        
        # Sort main blocks in reading order
        # Sort main blocks in reading order and other blocks per-page
        # Sort main blocks in reading order and other blocks per-page
        sorted_main_blocks = self.sort_main_blocks(all_main_blocks)
        sorted_other_blocks = sorted(all_other_blocks, key=lambda b: b['y_center'])

        # Annotate main blocks with a stable global index so images can reference them reliably
        for gi, mb in enumerate(sorted_main_blocks):
            mb['global_index'] = gi

        # Place images in the flow (this sets img['insert_after_block'] to a global index based on sorted_main_blocks)
        all_images = self.place_images(sorted_main_blocks, all_images)

        # Generate main markdown (independent)
        main_markdown = self.generate_main_content_markdown(
            sorted_main_blocks, title, main_content_area['main_font_size']
        )

        # Generate combined markdown first (this will mark other_blocks used and images inserted)
        combined_markdown = self.generate_combined_markdown(
            sorted_main_blocks,
            sorted_other_blocks,
            all_images,
            title,
            main_content_area['main_font_size'],
            main_content_area  # pass boundaries so we can decide column membership
        )

        # Now generate other markdown which will skip anything already used in combined
        other_markdown = self.generate_other_elements_markdown(
            sorted_other_blocks, all_images, title
        )



        
        return {
            'main': main_markdown,
            'other': other_markdown,
            'combined': combined_markdown
        }
    
    def save_chapter_markdown(self, first_page: int, last_page: int, chapter_title: str = None, output_filename: str = None) -> Dict[str, str]:
        """Extract pages and save to three separate markdown files"""
        markdown_contents = self.extract_chapter_to_markdown(first_page, last_page, chapter_title)
        
        # Generate base filename
        if output_filename:
            base_filename = output_filename
        else:
            base_filename = f"pages_{first_page}_{last_page}"
        
        # Save three files
        filepaths = {}
        
        # Main content file
        main_filename = f"{base_filename}_main.md"
        main_filepath = os.path.join(self.output_dir, main_filename)
        with open(main_filepath, 'w', encoding='utf-8') as f:
            f.write(markdown_contents['main'])
        filepaths['main'] = main_filepath
        
        # Other elements file
        other_filename = f"{base_filename}_other.md"
        other_filepath = os.path.join(self.output_dir, other_filename)
        with open(other_filepath, 'w', encoding='utf-8') as f:
            f.write(markdown_contents['other'])
        filepaths['other'] = other_filepath
        
        # Combined file
        combined_filename = f"{base_filename}_combined.md"
        combined_filepath = os.path.join(self.output_dir, combined_filename)
        with open(combined_filepath, 'w', encoding='utf-8') as f:
            f.write(markdown_contents['combined'])
        filepaths['combined'] = combined_filepath
        
        self.logger.info(f"Pages {first_page}-{last_page} saved to:")
        self.logger.info(f"  Main: {main_filepath}")
        self.logger.info(f"  Other: {other_filepath}")
        self.logger.info(f"  Combined: {combined_filepath}")
        
        return filepaths

import fitz  # PyMuPDF
from PIL import Image, ImageDraw

# Use the same pdf_path as in extractmarkdown.py
pdf_path = "breast-invasive-patient.pdf"  # <-- replace with the same path you set in extractmarkdown.py

def render_page_with_block_boxes(pdf_path, page_number, output_path="page_blocks.jpg", zoom=2):
    """
    Render a PDF page and draw block-level bounding boxes.

    Args:
        pdf_path (str): Path to the PDF file.
        page_number (int): Page number (0-indexed).
        output_path (str): Path to save the output JPEG.
        zoom (float): Zoom factor for higher resolution rendering.
    """
    # Open the PDF
    doc = fitz.open(pdf_path)
    if page_number < 0 or page_number >= len(doc):
        raise ValueError(f"Page number out of range. PDF has {len(doc)} pages.")

    page = doc[page_number]

    # Render page as a pixmap (image)
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)

    # Convert to a PIL Image
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    draw = ImageDraw.Draw(img)

    # Extract block-level text info
    text_dict = page.get_text("dict")
    for block in text_dict["blocks"]:
        if "lines" not in block:  # skip non-text blocks (e.g., images)
            continue
        bbox = block["bbox"]  # (x0, y0, x1, y1)
        # Scale coordinates by zoom
        x0, y0, x1, y1 = [v * zoom for v in bbox]
        # Draw rectangle
        draw.rectangle([x0, y0, x1, y1], outline="red", width=3)

    # Save the result
    img.save(output_path, "JPEG")
    print(f"Saved page {page_number+1} with block-level boxes to {output_path}")

# Example usage:
# render_page_with_block_boxes(pdf_path, 0, "page1_blocks.jpg")

def print_block_details(pdf_path, page_number):
    """
    Print all block-level bounding boxes and span properties (text, font, size, color).
    """
    doc = fitz.open(pdf_path)
    if page_number < 0 or page_number >= len(doc):
        raise ValueError(f"Page number out of range. PDF has {len(doc)} pages.")

    page = doc[page_number]
    text_dict = page.get_text("dict")

    for block_idx, block in enumerate(text_dict["blocks"]):
        if "lines" not in block:
            continue  # skip images or vector blocks
        print(f"\n=== Block {block_idx} ===")
        print(f"Block BBox: {block['bbox']}")
        for line in block["lines"]:
            for span in line["spans"]:
                text = span["text"].strip()
                if not text:
                    continue
                font = span.get("font", "Unknown")
                size = span.get("size", "Unknown")
                color = span.get("color", "Unknown")
                flags = span.get("flags", 0)
                print(f"  Text: {text}")
                print(f"    Font: {font}")
                print(f"    Size: {size}")
                print(f"    Color: {color}")
                print(f"    Flags: {flags}")

def main():
    """Example usage"""
    pdf_path = "breast-invasive-patient.pdf"
    extractor = PDFChapterExtractor(pdf_path, output_dir="nccn_markdowns\\Invasive Breast Cancer")
    
    try:
        filepaths = extractor.save_chapter_markdown(
            first_page=7, 
            last_page=7, 
            chapter_title="About invasive breast cancer",
            output_filename="chapter1"
        )
        print(f"Successfully extracted pages 7-7:")
        print(f"  Main content: {filepaths['main']}")
        print(f"  Other elements: {filepaths['other']}")
        print(f"  Combined: {filepaths['combined']}")
        
    except Exception as e:
        print(f"Error extracting pages: {e}")
        raise e
    finally:
        extractor.close_pdf()


if __name__ == "__main__":
    # Install required packages: pip install PyMuPDF pillow
    main()
    #render_page_with_block_boxes(pdf_path, 7, "page8_blocks.jpg")
    #print_block_details(pdf_path, 7)