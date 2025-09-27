import fitz  # PyMuPDF
import os

def extract_images_from_pdf(pdf_path, output_folder="nccn_ibc_images\\Met Breast Cancer"):
    """
    Extracts all images from a PDF file and saves them as JPEG files.
    
    Args:
        pdf_path (str): Path to the input PDF file.
        output_folder (str): Folder where images will be saved.
    """
    # Open the PDF
    doc = fitz.open(pdf_path)

    # Create output folder if it doesn't exist
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    image_count = 0
    for page_index, page in enumerate(doc, start=1):
        images = page.get_images(full=True)
        for img_index, img in enumerate(images, start=1):
            xref = img[0]
            pix = fitz.Pixmap(doc, xref)
            if pix.n > 4:  # Convert CMYK to RGB
                pix = fitz.Pixmap(fitz.csRGB, pix)
            image_count += 1
            image_path = os.path.join(output_folder, f"page{page_index}_img{img_index}.jpg")
            pix.save(image_path)
            print(f"Saved: {image_path}")

    print(f"\n✅ Extraction complete! {image_count} images saved in '{output_folder}'.")

# Example usage:
# Change "breast-invasive-patient.pdf" to the path of your PDF file
extract_images_from_pdf("breast-invasive-patient.pdf")
