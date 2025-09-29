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
        
        # You'll need to determine the main text color - check your debug output for dominant color
        # This is typically 0 (black) for main text, but headers might be a different value
        main_text_color = 0  # Adjust this based on your PDF's main text color

        for block_idx, block in enumerate(text_dict["blocks"]):
            if "lines" not in block:
                continue
            bbox = block['bbox']

            block_text = ""
            font_sizes = []
            colors = []
            span_details = []
            
            # Check if any span in this block is bold
            has_bold = False
            for line in block["lines"]:
                for span in line["spans"]:
                    block_text += span["text"]
                    font_sizes.append(span["size"])
                    colors.append(span["color"])
                    span_details.append(span)
                    if span.get("flags", 0) & 16:
                        has_bold = True

            if not block_text.strip():
                continue

            if self.should_exclude_text(block_text, bbox, page_height):
                continue

            avg_font_size = sum(font_sizes) / len(font_sizes) if font_sizes else 0
            dominant_color = max(set(colors), key=colors.count) if colors else 0

            # Column + font checks
            fits_left_col = abs(bbox[0] - boundaries["left_col_left"]) < TOLERANCE
            fits_right_col = abs(bbox[0] - boundaries["right_col_left"]) < TOLERANCE
            font_match = abs(avg_font_size - main_font_size) < 2
            
            # Check if this is a section header
            is_section_header = (
                avg_font_size > main_font_size + 2 and  # Bigger font size
                dominant_color != main_text_color and   # Different color (light blue headers)
                (fits_left_col or fits_right_col)       # Column aligned
            )

            # Classify blocks
            if is_section_header:
                # Section headers are treated as special main blocks
                main_blocks.append({
                    "text": block_text.strip(),
                    "bbox": bbox,
                    "font_size": avg_font_size,
                    "x_left": bbox[0],
                    "x_right": bbox[2],
                    "y_top": bbox[1],
                    "y_center": (bbox[1] + bbox[3]) / 2,
                    "page": page_num,
                    "column": "left" if fits_left_col else "right",
                    "is_bold": has_bold,
                    "is_header": True,  # Mark as header
                    "color": dominant_color
                })
            elif (fits_left_col or fits_right_col) and font_match:
                # Regular main content
                main_blocks.append({
                    "text": block_text.strip(),
                    "bbox": bbox,
                    "font_size": avg_font_size,
                    "x_left": bbox[0],
                    "x_right": bbox[2],
                    "y_top": bbox[1],
                    "y_center": (bbox[1] + bbox[3]) / 2,
                    "page": page_num,
                    "column": "left" if fits_left_col else "right",
                    "is_bold": has_bold,
                    "is_header": False,  # Regular content
                    "color": dominant_color
                })
            else:
                # Other content (captions, intro text, etc.)
                other_blocks.append({
                    "text": block_text.strip(),
                    "bbox": bbox,
                    "font_size": avg_font_size,
                    "x_left": bbox[0],
                    "x_right": bbox[2],
                    "y_top": bbox[1],
                    "y_center": (bbox[1] + bbox[3]) / 2,
                    "page": page_num,
                    "is_bold": has_bold,
                    "color": dominant_color
                })

        return main_blocks, other_blocks
    
    def sort_main_blocks(self, blocks: List[Dict]) -> List[Dict]:
        """Sort main blocks in reading order: page by page, left column then right column"""
        # Group by page
        by_page = {}
        for block in blocks:
            page = block.get('page', 0)
            by_page.setdefault(page, []).append(block)
        
        sorted_blocks = []
        for page in sorted(by_page.keys()):
            page_blocks = by_page[page]
            
            # Separate left and right column blocks
            left_blocks = [b for b in page_blocks if b.get('column') == 'left']
            right_blocks = [b for b in page_blocks if b.get('column') == 'right']
            
            # Sort each column by y-position (top to bottom)
            left_blocks.sort(key=lambda b: b.get('y_top', 0))
            right_blocks.sort(key=lambda b: b.get('y_top', 0))
            
            # Add left column first, then right column for this page
            sorted_blocks.extend(left_blocks)
            sorted_blocks.extend(right_blocks)
        
        return sorted_blocks


    
    def is_heading(self, text: str, font_size: float, main_font_size: float, is_bold: bool = False) -> bool:
        """Detect headings based on text and style"""
        text = text.strip()
        
        # Bold text that's reasonably short is likely a header
        if is_bold and len(text.split()) <= 12 and len(text) < 150:
            return True
        
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
    
    def format_caption_text(self, caption_text: str, font_size: float, main_font_size: float) -> str:
        """
        Format caption text based on its styling - detect headers vs body text
        """
        text = caption_text.strip()
        
        # Short text that's likely a caption header
        if len(text.split()) <= 5 and len(text) < 50:
            return f"### **{text}**"
        
        # Longer text that's likely caption body
        return f"**{text}**"

    
    def associate_other_blocks_with_images(self, other_blocks: List[Dict], images: List[Dict]) -> List[Dict]:
        """Associate other blocks with nearby images on the SAME PAGE"""
        for other_block in other_blocks:
            other_page = other_block.get('page', 0)
            other_y = other_block['y_center']
            
            # Find closest image ON THE SAME PAGE ONLY
            best_img = None
            min_distance = float('inf')
            
            for img in images:
                if not img.get('rect'):
                    continue
                
                # Check if image is on same page
                img_filename = img.get('filename', '')
                if f"page{other_page+1}_" not in img_filename:
                    continue  # Skip images from different pages
                
                # Now calculate distance (only for same-page images)
                img_y = img['y_center']
                distance = abs(other_y - img_y)
                
                if distance < min_distance:
                    min_distance = distance
                    best_img = img
            
            # Associate if close enough AND on same page
            if best_img and min_distance < 200:
                if 'captions' not in best_img:
                    best_img['captions'] = []
                
                caption_info = {
                    'text': other_block['text'],
                    'font_size': other_block.get('font_size', 12),
                    'is_header': len(other_block['text'].split()) <= 5 and len(other_block['text']) < 50
                }
                best_img['captions'].append(caption_info)
                other_block['used_as_caption'] = True
                print(f"[DEBUG] Marked block as caption: '{other_block['text'][:30]}...'")
        
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
        Generate markdown for main content blocks with proper bold formatting.
        """
        markdown = [f"# {chapter_title} - Main Content\n"]

        for idx, block in enumerate(main_blocks):
            text = block["text"]
            if not text:
                continue

            # Format text based on whether it should be bold
            if block.get("is_bold", False):
                text = f"**{text.strip()}**"

            if self.is_heading(text, block["font_size"], main_font_size, block.get("is_bold", False)):
                # Remove ** from headings since ## already makes them bold
                clean_text = text.replace("**", "").strip()
                markdown.append(f"## {clean_text}\n")
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

    
    # Add these debug prints to your generate_combined_markdown function

    

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
        Generate combined markdown in proper reading order: 
        beginning → page-by-page (left col → right col) → images per page
        """
        
        def normalize(s: str) -> str:
            return re.sub(r"\s+", " ", (s or "").strip())

        content = f"# {title}\n\n"
        
        # Step 1: Add beginning blocks (intro text from first page, top area only)
        if other_blocks:
            first_page = min(b.get('page', 0) for b in other_blocks)
            first_page_other = [b for b in other_blocks if b.get('page', 0) == first_page]
            
            # Get beginning blocks (top 25% of first page, left side)
            page_height = self.doc[first_page].rect.height if self.doc else 800
            beginning_blocks = []
            
            for ob in first_page_other:
                if ob.get('used_as_caption', False):
                    continue
                y_pos = ob.get('y_top', ob.get('y_center', 0))
                x_pos = ob.get('x_left', 0)
                
                # Only take blocks from top-left area of first page
                if (y_pos < page_height * 0.35 and 
                    x_pos < boundaries.get('left_col_right', 300)):
                    beginning_blocks.append(ob)
            
            # Add beginning paragraph
            if beginning_blocks:
                beginning_text = " ".join([normalize(b["text"]) for b in beginning_blocks])
                content += f"{beginning_text}\n\n"
                
                # Mark as used
                for b in beginning_blocks:
                    b["used"] = True
                
                print(f"[COMBINED] Added beginning section with {len(beginning_blocks)} blocks")
        
        # Step 2: Process main blocks (headers + content) in reading order
        sorted_main = self.sort_main_blocks(main_blocks)
        
        # Group main blocks by page for processing
        main_by_page = {}
        for mb in sorted_main:
            page = mb.get('page', 0)
            main_by_page.setdefault(page, []).append(mb)
        
        # Process each page in order
        pages = sorted(main_by_page.keys())
        for page in pages:
            print(f"[COMBINED] Processing page {page + 1}")
            page_main_blocks = main_by_page[page]
            
            # Process all main blocks (headers and content) for this page in order
            for block in page_main_blocks:
                text = normalize(block.get("text", ""))
                if not text:
                    continue
                
                # Check if this is a header
                if block.get("is_header", False):
                    content += f"## {text}\n\n"
                    print(f"[COMBINED] Added header: {text[:50]}")
                else:
                    # Apply bold formatting if needed
                    if block.get("is_bold", False):
                        text = f"**{text}**"
                    content += f"{text}\n\n"
                    print(f"[COMBINED] Added content: {text[:50]}")
            
            # Add any leftover other blocks for this page (excluding captions and used blocks)
            page_other = [b for b in other_blocks if b.get('page') == page]
            leftover_blocks = []
            
            for ob in page_other:
                if (ob.get('used', False) or 
                    ob.get('used_as_caption', False)):
                    continue
                leftover_blocks.append(ob)
            
            # Add leftover blocks
            for ob in leftover_blocks:
                text = normalize(ob.get("text", ""))
                if text:
                    content += f"{text}\n\n"
                    ob["used"] = True
                    print(f"[COMBINED] Added leftover: {text[:50]}")
            
            # Step 3: Add images for this page at the end
            page_images = [img for img in images if f"page{page+1}_" in img.get("filename", "")]
            
            for img in page_images:
                if img.get("inserted", False):
                    continue
                    
                img_md = f"![Image](images/{img['filename']})"
                
                # Add captions
                if img.get("captions"):
                    for cap in img["captions"]:
                        if isinstance(cap, dict):
                            cap_text = normalize(cap['text'])
                            if cap.get('is_header', False):
                                img_md += f"\n### **{cap_text}**"
                            else:
                                img_md += f"\n**{cap_text}**"
                        else:
                            cap_text = normalize(cap)
                            if len(cap_text.split()) <= 5:
                                img_md += f"\n### **{cap_text}**"
                            else:
                                img_md += f"\n**{cap_text}**"
                
                content += f"\n{img_md}\n\n"
                img["inserted"] = True
                print(f"[COMBINED] Added image for page {page + 1}: {img['filename']}")
        
        return content








    def debug_page_analysis(self, page_num: int):
        """Debug function to analyze a single page in detail"""
        if not self.doc:
            self.open_pdf()
        
        page = self.doc[page_num]
        text_dict = page.get_text("dict")
        page_height = page.rect.height
        page_width = page.rect.width
        
        print(f"\n=== DEBUG PAGE {page_num + 1} ===")
        print(f"Page dimensions: {page_width} x {page_height}")
        
        # Collect all text blocks with their properties
        blocks_info = []
        for block_idx, block in enumerate(text_dict["blocks"]):
            if "lines" not in block:
                continue
            
            bbox = block['bbox']
            block_text = ""
            font_sizes = []
            
            for line in block["lines"]:
                for span in line["spans"]:
                    block_text += span["text"]
                    font_sizes.append(span["size"])
            
            if not block_text.strip():
                continue
                
            avg_font_size = sum(font_sizes) / len(font_sizes) if font_sizes else 0
            
            blocks_info.append({
                'idx': block_idx,
                'text': block_text.strip()[:60] + "..." if len(block_text.strip()) > 60 else block_text.strip(),
                'bbox': bbox,
                'font_size': avg_font_size,
                'x_left': bbox[0],
                'x_right': bbox[2],
                'y_top': bbox[1],
                'width': bbox[2] - bbox[0],
                'excluded': self.should_exclude_text(block_text, bbox, page_height)
            })
        
        # Sort by y-position for visual order
        blocks_info.sort(key=lambda b: b['y_top'])
        
        print(f"\nFound {len(blocks_info)} text blocks:")
        print(f"{'Idx':<3} {'X-Left':<6} {'X-Right':<7} {'Width':<6} {'Font':<5} {'Excl':<4} {'Text'}")
        print("-" * 80)
        
        for block in blocks_info:
            print(f"{block['idx']:<3} {block['x_left']:<6.0f} {block['x_right']:<7.0f} "
                f"{block['width']:<6.0f} {block['font_size']:<5.1f} {block['excluded']!s:<4} {block['text']}")
        
        return blocks_info

    def debug_column_detection(self, page_num: int):
        """Debug the column detection algorithm"""
        if not self.doc:
            self.open_pdf()
        
        # Run your existing analysis
        style_analysis = self.analyze_text_styles([page_num])
        boundaries = self.detect_main_content_area(style_analysis)
        
        print(f"\n=== COLUMN DETECTION DEBUG ===")
        print(f"Detected boundaries: {boundaries}")
        
        # Show which blocks fit each column
        page = self.doc[page_num]
        text_dict = page.get_text("dict")
        page_height = page.rect.height
        
        left_col_blocks = []
        right_col_blocks = []
        unclassified_blocks = []
        
        for block_idx, block in enumerate(text_dict["blocks"]):
            if "lines" not in block:
                continue
            
            bbox = block['bbox']
            block_text = ""
            font_sizes = []
            
            for line in block["lines"]:
                for span in line["spans"]:
                    block_text += span["text"]
                    font_sizes.append(span["size"])
            
            if not block_text.strip() or self.should_exclude_text(block_text, bbox, page_height):
                continue
                
            avg_font_size = sum(font_sizes) / len(font_sizes) if font_sizes else 0
            
            # Test column classification with different tolerances
            fits_left_strict = abs(bbox[0] - boundaries["left_col_left"]) < TOLERANCE
            fits_right_strict = abs(bbox[0] - boundaries["right_col_left"]) < TOLERANCE
            fits_left_loose = abs(bbox[0] - boundaries["left_col_left"]) < 30
            fits_right_loose = abs(bbox[0] - boundaries["right_col_left"]) < 30
            font_match = abs(avg_font_size - boundaries['main_font_size']) < 2
            
            block_info = {
                'text': block_text.strip()[:50] + "..." if len(block_text.strip()) > 50 else block_text.strip(),
                'x_left': bbox[0],
                'font_size': avg_font_size,
                'fits_left_strict': fits_left_strict,
                'fits_right_strict': fits_right_strict,
                'fits_left_loose': fits_left_loose,
                'fits_right_loose': fits_right_loose,
                'font_match': font_match
            }
            
            if fits_left_strict and font_match:
                left_col_blocks.append(block_info)
            elif fits_right_strict and font_match:
                right_col_blocks.append(block_info)
            else:
                unclassified_blocks.append(block_info)
        
        print(f"\nLEFT COLUMN BLOCKS ({len(left_col_blocks)}):")
        for block in left_col_blocks:
            print(f"  X:{block['x_left']:.0f} Font:{block['font_size']:.1f} - {block['text']}")
        
        print(f"\nRIGHT COLUMN BLOCKS ({len(right_col_blocks)}):")
        for block in right_col_blocks:
            print(f"  X:{block['x_left']:.0f} Font:{block['font_size']:.1f} - {block['text']}")
        
        print(f"\nUNCLASSIFIED BLOCKS ({len(unclassified_blocks)}):")
        for block in unclassified_blocks:
            print(f"  X:{block['x_left']:.0f} Font:{block['font_size']:.1f} "
                f"L-Loose:{block['fits_left_loose']} R-Loose:{block['fits_right_loose']} "
                f"Font-Match:{block['font_match']} - {block['text']}")

    def debug_quick_fix(self):
        """Quick debugging session for the problem page"""
        print("Running comprehensive debug for page 7...")
        self.debug_page_analysis(6)  # Page 7 is index 6
        self.debug_column_detection(6)

    def debug_block_properties(self, first_page: int, last_page: int):
        """Debug function to analyze block properties across pages"""
        if not self.doc:
            self.open_pdf()
        
        chapter_pages = self.get_chapter_pages(first_page, last_page)
        
        print(f"\n=== BLOCK PROPERTIES DEBUG (Pages {first_page}-{last_page}) ===")
        
        for page_num in chapter_pages:
            page = self.doc[page_num]
            text_dict = page.get_text("dict")
            page_height = page.rect.height
            
            print(f"\n--- PAGE {page_num + 1} ---")
            
            for block_idx, block in enumerate(text_dict["blocks"]):
                if "lines" not in block:
                    continue
                bbox = block['bbox']
                
                block_text = ""
                font_sizes = []
                colors = []
                
                for line in block["lines"]:
                    for span in line["spans"]:
                        block_text += span["text"]
                        font_sizes.append(span["size"])
                        colors.append(span["color"])
                
                if not block_text.strip() or self.should_exclude_text(block_text, bbox, page_height):
                    continue
                
                avg_font_size = sum(font_sizes) / len(font_sizes) if font_sizes else 0
                dominant_color = max(set(colors), key=colors.count) if colors else 0
                
                # Show first 50 chars of text
                text_preview = block_text.strip()[:50] + "..." if len(block_text.strip()) > 50 else block_text.strip()
                
                print(f"Block {block_idx:2d}: Font={avg_font_size:5.1f} Color={dominant_color:8.0f} X={bbox[0]:5.0f} Text: '{text_preview}'")
                
                # Flag potential headers
                if avg_font_size > 14:  # Adjust threshold as needed
                    print(f"         ^^^ POTENTIAL HEADER (large font)")
                if dominant_color != 0:  # Assuming 0 is black text
                    print(f"         ^^^ POTENTIAL HEADER (different color)")


    # Usage:
    # extractor = PDFChapterExtractor("breast-invasive-patient.pdf")
    # extractor.debug_quick_fix()


    
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

    def debug_image_caption_association(self, first_page: int, last_page: int):
        """Debug how captions are being associated with images"""
        if not self.doc:
            self.open_pdf()
        
        chapter_pages = self.get_chapter_pages(first_page, last_page)
        style_analysis = self.analyze_text_styles(chapter_pages)
        main_content_area = self.detect_main_content_area(style_analysis)
        
        all_other_blocks = []
        all_images = []
        
        # Process each page
        for page_num in chapter_pages:
            page = self.doc[page_num]
            main_blocks, other_blocks = self.classify_text_blocks(
                page, page_num, main_content_area['main_font_size'], main_content_area
            )
            page_images = self.extract_images_from_page(page, page_num)
            
            all_other_blocks.extend(other_blocks)
            all_images.extend(page_images)
        
        print(f"\n=== IMAGE CAPTION ASSOCIATION DEBUG ===")
        print(f"\nTotal images: {len(all_images)}")
        print(f"Total other blocks: {len(all_other_blocks)}")
        
        # Show what we have
        for img in all_images:
            print(f"\nImage: {img['filename']} (page from filename)")
            print(f"  Y-center: {img['y_center']:.1f}")
        
        for i, ob in enumerate(all_other_blocks):
            print(f"\nOther Block {i}: page={ob.get('page', 'MISSING')+1} y={ob['y_center']:.1f}")
            print(f"  Text: '{ob['text'][:60]}...'")
        
        # Now run the association
        print(f"\n=== RUNNING ASSOCIATION ===")
        self.associate_other_blocks_with_images(all_other_blocks, all_images)
        
        # Show results
        print(f"\n=== ASSOCIATION RESULTS ===")
        for img in all_images:
            print(f"\nImage: {img['filename']}")
            if img.get('captions'):
                print(f"  Has {len(img['captions'])} captions:")
                for cap in img['captions']:
                    if isinstance(cap, dict):
                        print(f"    - '{cap['text'][:60]}...'")
                    else:
                        print(f"    - '{cap[:60]}...'")
            else:
                print(f"  No captions")
    
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
            last_page=9, 
            chapter_title="About invasive breast cancer",
            output_filename="chapter1"
        )
        print(f"Successfully extracted pages 7-9:")
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
    #pdf_path = "breast-invasive-patient.pdf"
    #extractor = PDFChapterExtractor(pdf_path, output_dir="nccn_markdowns\\Invasive Breast Cancer")
    #extractor.debug_image_caption_association(7, 9)
    #render_page_with_block_boxes(pdf_path, 8, "page9_blocks.jpg")
    #print_block_details(pdf_path, 7)
    #pdf_path = "breast-invasive-patient.pdf"
    #extractor = PDFChapterExtractor(pdf_path, output_dir="nccn_markdowns\\Invasive Breast Cancer")
    #extractor.debug_quick_fix()
