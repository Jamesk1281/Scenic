import AVFoundation
import Foundation

// Offline synthesis: no audio device, nothing played, just how many frames the
// renderer produces for a phrase. `write` calls back on the main run loop, so
// the loop has to be pumped rather than blocked on a semaphore.
let synth = AVSpeechSynthesizer()
let voice = AVSpeechSynthesisVoice(language: "en-US")

var lines: [String] = []
while let line = readLine(strippingNewline: true) {
    if !line.isEmpty { lines.append(line) }
}

func duration(_ text: String) -> Double {
    let utterance = AVSpeechUtterance(string: text)
    utterance.voice = voice
    utterance.rate = AVSpeechUtteranceDefaultSpeechRate
    var frames = 0.0
    var rate = 22050.0
    var finished = false
    synth.write(utterance) { buffer in
        guard let pcm = buffer as? AVAudioPCMBuffer else { return }
        if pcm.frameLength == 0 { finished = true; return }
        rate = pcm.format.sampleRate
        frames += Double(pcm.frameLength)
    }
    let deadline = Date().addingTimeInterval(20)
    while !finished, Date() < deadline {
        RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.02))
    }
    return frames / rate
}

for line in lines {
    print(String(format: "%.3f\t%@", duration(line), line))
}
