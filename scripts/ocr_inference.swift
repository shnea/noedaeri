// Native, per-job Vision runner. Input paths and recognized text never go to stdout.
import Foundation
import Vision
import ImageIO
import Darwin

enum RecognitionError: Error { case invalid, tooLarge }

func recognize(_ requestFile: String) throws -> [String: Any] {
    let started = Date()
    let data = try Data(contentsOf: URL(fileURLWithPath: requestFile))
    guard data.count <= 65536,
          let input = try JSONSerialization.jsonObject(with: data) as? [String: Any],
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
} catch RecognitionError.tooLarge {
    print("{\"error\":\"ocr_result_too_large\"}")
} catch {
    print("{\"error\":\"ocr_recognition_failed\"}")
}
