import AVFoundation
import XCTest
@testable import Scenic

/// Which voices get offered, and what choosing one does to the schedule.
///
/// The filter is a measurement, so most of this runs the measurement — it is
/// fast (offline synthesis, no audio session, no sound) and it is the only
/// thing that can tell a usable voice from one that sings.
@MainActor
final class VoiceCatalogueTests: XCTestCase {

    override func setUp() {
        super.setUp()
        VoiceCatalogue.selectedIdentifier = nil
    }
    override func tearDown() {
        VoiceCatalogue.selectedIdentifier = nil
        super.tearDown()
    }

    func test_a_real_voice_takes_about_as_long_as_the_thresholds_assume() async throws {
        guard let samantha = AVSpeechSynthesisVoice.speechVoices()
            .first(where: { $0.name == "Samantha" }) else {
            throw XCTSkip("Samantha is not installed on this runtime")
        }
        let seconds = await VoiceCatalogue.duration(of: samantha)
        // Measured 1.72 s. The band is wide enough to survive Apple retuning
        // the voice and narrow enough to catch the renderer returning nothing,
        // which is what a voice listed but not installed does.
        XCTAssertGreaterThan(seconds, 1.0)
        XCTAssertLessThan(seconds, 2.5)
    }

    func test_the_novelty_voices_are_not_offered() async {
        let offered = await VoiceCatalogue.usable()
        XCTAssertFalse(offered.isEmpty, "no usable voice at all")

        // Two gates, and each catches something the other cannot. "Good News"
        // sings the sentence over 6.15 s and would arrive after the turn;
        // "Bahh" is a sheep that measures 2.38 s, indistinguishable by duration
        // from a slow premium voice and obvious by provenance.
        for name in ["Good News", "Bad News", "Bells", "Jester", "Cellos", "Organ",
                     "Bahh", "Boing", "Albert", "Bubbles", "Zarvox", "Trinoids"] {
            guard AVSpeechSynthesisVoice.speechVoices().contains(where: { $0.name == name })
            else { continue }
            XCTAssertFalse(offered.contains { $0.name == name },
                           "\(name) has no business reading directions in a car")
        }
        // Nothing offered comes from the MacinTalk namespace.
        for option in offered {
            XCTAssertTrue(VoiceCatalogue.namespaces.contains { option.identifier.hasPrefix($0) },
                          "\(option.name) is from \(option.identifier)")
        }
        // And every voice that *is* offered is inside the budget by
        // construction, which is the invariant the picker relies on.
        for option in offered {
            XCTAssertLessThanOrEqual(option.seconds, VoiceCatalogue.budget, option.name)
            XCTAssertGreaterThan(option.seconds, 0, option.name)
        }
    }

    func test_an_ordinary_speech_voice_survives_the_filter() async {
        let offered = await VoiceCatalogue.usable()
        let names = Set(offered.map(\.name))
        let expected = ["Samantha", "Karen", "Daniel", "Moira", "Tessa", "Rishi"]
            .filter { name in
                AVSpeechSynthesisVoice.speechVoices().contains { $0.name == name }
            }
        for name in expected {
            XCTAssertTrue(names.contains(name), "\(name) should be offered")
        }
        XCTAssertFalse(expected.isEmpty, "no reference voice installed to check against")
    }

    func test_a_voice_that_has_been_deleted_falls_back_rather_than_going_mute() {
        VoiceCatalogue.selectedIdentifier = "com.apple.voice.that.was.deleted"
        XCTAssertNotNil(VoiceCatalogue.selectedVoice(),
                        "a stale identifier must not leave the app with no voice")
    }

    func test_choosing_a_voice_persists_it_reaches_the_speaker_and_is_sampled() {
        let speaker = VoiceGuideTests.FakeSpeaker()
        let guide = VoiceGuide(speaker: speaker, muted: true)
        let choice = VoiceCatalogue.Measured(
            identifier: "com.apple.voice.super-compact.en-GB.Daniel", name: "Daniel",
            language: "en-GB", seconds: 1.80, quality: "")

        guide.useVoice(choice)

        XCTAssertEqual(VoiceCatalogue.selectedIdentifier, choice.identifier)
        XCTAssertNotNil(speaker.voiceUsed, "the speaker was never told")
        // Unmuted and sampled: picking a voice while silenced and hearing
        // nothing reads as the picker being broken.
        XCTAssertFalse(guide.muted)
        XCTAssertEqual(speaker.said, [VoiceCatalogue.reference + "."])
    }

    // MARK: - What a slower voice does to the schedule

    func test_a_slower_voice_starts_the_words_earlier_by_exactly_its_extra_length() {
        let speaker = VoiceGuideTests.FakeSpeaker()
        let guide = VoiceGuide(speaker: speaker, muted: false)
        XCTAssertEqual(guide.finalAt, VoiceGuide.referenceFinalAt, accuracy: 1e-9)

        // Half a second longer than the 1.83 s the thresholds were derived for.
        guide.useVoice(VoiceCatalogue.Measured(identifier: "x", name: "Slow",
                                               language: "en-US", seconds: 2.33,
                                               quality: ""))
        XCTAssertEqual(guide.finalAt, VoiceGuide.referenceFinalAt + 0.5, accuracy: 1e-9)
        XCTAssertEqual(guide.chainWithin, VoiceGuide.referenceChainWithin + 0.5, accuracy: 1e-9)
        // Both utterances stretch, so the room the pair needs does twice.
        XCTAssertEqual(guide.prepareFloor, VoiceGuide.referencePrepareFloor + 1.0, accuracy: 1e-9)
    }

    func test_a_faster_voice_does_not_tighten_the_thresholds() {
        let guide = VoiceGuide(speaker: VoiceGuideTests.FakeSpeaker(), muted: false)
        guide.useVoice(VoiceCatalogue.Measured(identifier: "x", name: "Quick",
                                               language: "en-US", seconds: 1.20,
                                               quality: ""))
        // The reference values were measured and validated against the corpus;
        // a voice being quicker is not evidence for cutting the clearance a
        // driver needs after the words.
        XCTAssertEqual(guide.finalAt, VoiceGuide.referenceFinalAt, accuracy: 1e-9)
        XCTAssertEqual(guide.chainWithin, VoiceGuide.referenceChainWithin, accuracy: 1e-9)
        XCTAssertEqual(guide.prepareFloor, VoiceGuide.referencePrepareFloor, accuracy: 1e-9)
    }

    func test_the_label_says_what_a_driver_needs_to_tell_two_voices_apart() {
        let plain = VoiceCatalogue.Measured(identifier: "a", name: "Samantha",
                                            language: "en-US", seconds: 1.7, quality: "")
        let british = VoiceCatalogue.Measured(identifier: "b", name: "Daniel",
                                              language: "en-GB", seconds: 1.8, quality: "")
        let posh = VoiceCatalogue.Measured(identifier: "c", name: "Ava",
                                           language: "en-US", seconds: 2.1,
                                           quality: "Premium")
        XCTAssertEqual(plain.label, "Samantha", "no en-US noise on every row")
        XCTAssertEqual(british.label, "Daniel (en-GB)")
        XCTAssertEqual(posh.label, "Ava · Premium")
        XCTAssertTrue(posh.isDownloaded)
        XCTAssertFalse(plain.isDownloaded)
    }
}
