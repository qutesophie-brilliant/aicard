# Upload content checks

Consumer uploads are checked by `app/upload_safety.py` before OCR. The entire
batch must pass before any image is sent to an external AI service.

- Check actual image format against the filename and enforce file/pixel limits.
- Re-encode the visible image pixels as PNG, removing metadata and trailing data.
- Parse PDFs and reject JavaScript, launch/external actions, embedded files,
  XFA, and active multimedia. Parse PDF name escapes and compressed objects.
- Preserve ordinary source hyperlinks and text. Rebuild accepted PDFs without
  metadata, unused objects, or trailing data.
- Bound page/image/object counts and PDF raster dimensions before OCR.

This is format validation, active-content rejection, and content reconstruction.
It is not a signature-based antivirus or a guarantee that all malicious files
can be detected. No ClamAV service or new external scanning provider is used.
The website should describe the implemented checks without claiming a complete
antivirus scan.
