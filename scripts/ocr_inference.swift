// Native, per-job Vision runner. Input paths and recognized text never go to stdout.
import Foundation
import Vision
import ImageIO
import PDFKit
import CoreGraphics
import Darwin

enum RecognitionError: Error { case invalid, tooLarge, encrypted, pages }

func imageLines(_ image: CGImage, _ language: String, _ correction: Bool) throws -> ([[String: Any]], [String]) {
    let languages = ["auto": ["ko-KR", "en-US"], "ko": ["ko-KR", "en-US"],
                     "en": ["en-US"], "ja": ["ja-JP", "en-US"],
                     "zh-Hans": ["zh-Hans", "en-US"], "zh-Hant": ["zh-Hant", "en-US"]]
    guard let selected = languages[language] else { throw RecognitionError.invalid }
    let request = VNRecognizeTextRequest()
    request.revision = VNRecognizeTextRequestRevision3
    request.recognitionLevel = .accurate
    request.recognitionLanguages = selected
    request.automaticallyDetectsLanguage = language == "auto"
    request.usesLanguageCorrection = correction
    guard Set(selected).isSubset(of: Set(try request.supportedRecognitionLanguages()))
    else { throw RecognitionError.invalid }
    try VNImageRequestHandler(cgImage: image).perform([request])
    let observations = request.results ?? []
    guard observations.count <= 10000 else { throw RecognitionError.tooLarge }
    // Preserve Vision's observation order; this is not a table/layout reconstruction.
    let lines: [[String: Any]] = observations.compactMap { observation in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let box = observation.boundingBox
        return ["text": candidate.string, "confidence": candidate.confidence,
                "bounding_box": ["left": box.minX, "top": 1 - box.maxY,
                                 "width": box.width, "height": box.height]]
    }
    return (lines, selected)
}

func recognize(_ requestFile: String) throws -> [String: Any] {
    let started = Date()
    let data = try Data(contentsOf: URL(fileURLWithPath: requestFile))
    guard data.count <= 65536,
          let input = try JSONSerialization.jsonObject(with: data) as? [String: Any]
    else { throw RecognitionError.invalid }
    if input["mode"] as? String == "pdf" { return try recognizePDF(input) }
    guard
          let source = input["source"] as? String,
          let output = input["output"] as? String,
          let language = input["language"] as? String,
          let correction = input["language_correction"] as? Bool,
          let sourceInfo = input["source_info"] as? [String: Any],
          let imageSource = CGImageSourceCreateWithURL(URL(fileURLWithPath: source) as CFURL, nil),
          let properties = CGImageSourceCopyPropertiesAtIndex(imageSource, 0, nil) as? [CFString: Any],
          let width = properties[kCGImagePropertyPixelWidth] as? Int,
          let height = properties[kCGImagePropertyPixelHeight] as? Int,
          width > 0, height > 0, width <= 4096, height <= 4096,
          let image = CGImageSourceCreateImageAtIndex(imageSource, 0, nil)
    else { throw RecognitionError.invalid }
    let (lines, selected) = try imageLines(image, language, correction)
    let result: [String: Any] = [
        "text": lines.compactMap { $0["text"] as? String }.joined(separator: "\n"),
        "lines": lines, "source": sourceInfo, "language": language,
        "recognition_languages": selected, "language_correction": correction,
        "engine": "Apple Vision", "revision": 3, "coordinate_system": "normalized_top_left",
        "line_order": "vision_observations"
    ]
    let encoded = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
    guard encoded.count <= 4 * 1024 * 1024 else { throw RecognitionError.tooLarge }
    try encoded.write(to: URL(fileURLWithPath: output), options: [.atomic])
    try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: output)
    var usage = rusage()
    getrusage(RUSAGE_SELF, &usage)
    return ["elapsed_seconds": Date().timeIntervalSince(started),
            "peak_memory_bytes": usage.ru_maxrss]
}

func recognizePDF(_ input: [String: Any]) throws -> [String: Any] {
    guard let source = input["source"] as? String,
          let output = input["output"] as? String,
          let progress = input["progress"] as? String,
          let language = input["language"] as? String,
          let correction = input["language_correction"] as? Bool,
          let mode = input["extraction"] as? String, ["auto", "ocr", "text"].contains(mode),
          let maxPages = input["max_pages"] as? Int, (1...500).contains(maxPages),
          let document = PDFDocument(url: URL(fileURLWithPath: source))
    else { throw RecognitionError.invalid }
    guard !document.isEncrypted && !document.isLocked else { throw RecognitionError.encrypted }
    let count = document.pageCount
    guard count > 0, count <= maxPages else { throw RecognitionError.pages }
    var pages = [[String: Any]](), text = [String](), totalBytes = 0, totalLines = 0
    func writeProgress(_ completed: Int) throws {
        let bytes = try JSONSerialization.data(withJSONObject: ["completed": completed, "total": count])
        try bytes.write(to: URL(fileURLWithPath: progress), options: [.atomic])
    }
    try writeProgress(0)
    for index in 0..<count {
        let result: [String: Any] = try autoreleasepool {
            guard let page = document.page(at: index) else { throw RecognitionError.invalid }
            let extracted = mode == "ocr" ? "" : (page.string ?? "")
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if mode == "text" || !extracted.isEmpty {
                return ["page": index + 1, "method": "text", "text": extracted, "lines": []]
            }
            guard let ref = page.pageRef else { throw RecognitionError.invalid }
            let bounds = page.bounds(for: .cropBox)
            guard bounds.width.isFinite, bounds.height.isFinite,
                  bounds.width > 0, bounds.height > 0,
                  bounds.width <= 100000, bounds.height <= 100000
            else { throw RecognitionError.invalid }
            // Render at up to 144dpi, within 4096px per side; one page lives at a time.
            let scale = min(2, 4096 / max(bounds.width, bounds.height))
            let width = max(1, Int(ceil(bounds.width * scale)))
            let height = max(1, Int(ceil(bounds.height * scale)))
            guard let context = CGContext(data: nil, width: width, height: height,
                bitsPerComponent: 8, bytesPerRow: width * 4,
                space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)
            else { throw RecognitionError.invalid }
            context.setFillColor(CGColor(gray: 1, alpha: 1))
            let rect = CGRect(x: 0, y: 0, width: width, height: height)
            context.fill(rect)
            context.concatenate(ref.getDrawingTransform(.cropBox, rect: rect,
                rotate: 0, preserveAspectRatio: true))
            context.drawPDFPage(ref)
            guard let image = context.makeImage() else { throw RecognitionError.invalid }
            let (lines, _) = try imageLines(image, language, correction)
            return ["page": index + 1, "method": "ocr",
                "text": lines.compactMap { $0["text"] as? String }.joined(separator: "\n"),
                "lines": lines, "width": width, "height": height]
        }
        let encoded = try JSONSerialization.data(withJSONObject: result)
        totalBytes += encoded.count
        totalLines += (result["lines"] as? [[String: Any]])?.count ?? 0
        guard totalBytes <= 1800000, totalLines <= 10000 else { throw RecognitionError.tooLarge }
        pages.append(result)
        text.append(result["text"] as? String ?? "")
        try writeProgress(index + 1)
    }
    let result: [String: Any] = ["version": 1, "engine": "PDFKit + Apple Vision", "revision": 3,
        "mode": mode, "page_count": count, "pages": pages, "text": text.joined(separator: "\n\n"),
        "coordinate_system": "normalized_top_left", "line_order": "vision_observations",
        "language": language, "language_correction": correction]
    let encoded = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
    guard encoded.count <= 4 * 1024 * 1024 else { throw RecognitionError.tooLarge }
    try encoded.write(to: URL(fileURLWithPath: output), options: [.atomic])
    try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: output)
    var usage = rusage(); getrusage(RUSAGE_SELF, &usage)
    return ["peak_memory_bytes": usage.ru_maxrss]
}

do {
    let receipt: [String: Any]
    if CommandLine.arguments.count == 2, CommandLine.arguments[1] == "--probe" {
        let request = VNRecognizeTextRequest()
        request.revision = VNRecognizeTextRequestRevision3
        request.recognitionLevel = .accurate
        receipt = ["revision": 3, "languages": try request.supportedRecognitionLanguages()]
    } else if CommandLine.arguments.count == 2 {
        receipt = try autoreleasepool { try recognize(CommandLine.arguments[1]) }
    } else {
        throw RecognitionError.invalid
    }
    let encoded = try JSONSerialization.data(withJSONObject: receipt, options: [.sortedKeys])
    print(String(decoding: encoded, as: UTF8.self))
} catch RecognitionError.encrypted {
    print("{\"error\":\"pdf_encrypted\"}")
} catch RecognitionError.pages {
    print("{\"error\":\"pdf_page_limit_exceeded\"}")
} catch RecognitionError.tooLarge {
    print("{\"error\":\"ocr_result_too_large\"}")
} catch {
    print("{\"error\":\"ocr_recognition_failed\"}")
}
