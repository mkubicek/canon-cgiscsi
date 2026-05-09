import Foundation
import Vision

struct PageResult {
    let path: String
    let lines: Int
    let words: Int
    let weightedConfidence: Double
    let elapsed: Double
}

func recognize(path: String) throws -> PageResult {
    let url = URL(fileURLWithPath: path)
    let start = Date()
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    request.recognitionLanguages = ["de-DE", "en-US"]

    let handler = VNImageRequestHandler(url: url, options: [:])
    try handler.perform([request])

    let observations = request.results ?? []
    var wordCount = 0
    var weightedConfidence = 0.0

    for observation in observations {
        guard let candidate = observation.topCandidates(1).first else {
            continue
        }
        let words = candidate.string.split { $0.isWhitespace || $0.isNewline }
        let count = words.count
        wordCount += count
        weightedConfidence += Double(candidate.confidence) * Double(max(count, 1))
    }

    let denominator = max(wordCount, observations.count, 1)
    return PageResult(
        path: path,
        lines: observations.count,
        words: wordCount,
        weightedConfidence: weightedConfidence / Double(denominator),
        elapsed: Date().timeIntervalSince(start)
    )
}

let paths = CommandLine.arguments.dropFirst()
if paths.isEmpty {
    FileHandle.standardError.write(Data("usage: swift apple_vision_ocr.swift PAGE.jpg...\n".utf8))
    exit(2)
}

var results: [PageResult] = []
let totalStart = Date()
for path in paths {
    do {
        results.append(try recognize(path: path))
    } catch {
        FileHandle.standardError.write(Data("\(path): \(error)\n".utf8))
        exit(1)
    }
}

let totalWords = results.reduce(0) { $0 + $1.words }
let totalLines = results.reduce(0) { $0 + $1.lines }
let weighted = results.reduce(0.0) { $0 + ($1.weightedConfidence * Double(max($1.words, 1))) }
let denominator = results.reduce(0) { $0 + max($1.words, 1) }

print("page\tlines\twords\tconf\tseconds\tpath")
for result in results {
    print("\((result.path as NSString).lastPathComponent)\t\(result.lines)\t\(result.words)\t\(String(format: "%.3f", result.weightedConfidence))\t\(String(format: "%.3f", result.elapsed))\t\(result.path)")
}
print("TOTAL\t\(totalLines)\t\(totalWords)\t\(String(format: "%.3f", weighted / Double(max(denominator, 1))))\t\(String(format: "%.3f", Date().timeIntervalSince(totalStart)))")
