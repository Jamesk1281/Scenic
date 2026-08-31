import XCTest

/// Drives the whole device run, because `devicectl` cannot: it refuses to
/// launch anything on a locked phone ("the device was not, or could not be,
/// unlocked"), and this phone auto-locks in about a minute. XCUITest can wake
/// it, answer the location prompt, and press Home; the locked state then
/// arrives on its own if nothing touches the device afterwards.
///
/// `SPIKE_BUNDLE` chooses the variant so one runner serves both and only one
/// of the three app slots a free developer profile allows is spent on it.
final class SpikeDriveTests: XCTestCase {

    private var backgroundSeconds: TimeInterval {
        Double(ProcessInfo.processInfo.environment["SPIKE_BACKGROUND_SECONDS"] ?? "") ?? 300
    }

    func testForegroundThenBackgroundThenLocked() {
        let bundle = ProcessInfo.processInfo.environment["SPIKE_BUNDLE"]
            ?? "app.scenic.spike.loc"
        let app = XCUIApplication(bundleIdentifier: bundle)
        app.launch()

        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        for label in ["Allow While Using App", "Allow Once", "Allow"] {
            let button = springboard.buttons[label]
            if button.waitForExistence(timeout: 6) {
                button.tap()
                break
            }
        }
        XCTAssertTrue(app.staticTexts["Voice spike"].waitForExistence(timeout: 20),
                      "spike did not come up")

        // Foreground: three or four utterances at the 12 s cadence.
        Thread.sleep(forTimeInterval: 45)

        // Backgrounded, and then left completely alone — no queries, no
        // screenshots — so nothing this test does keeps the screen awake.
        XCUIDevice.shared.press(.home)
        Thread.sleep(forTimeInterval: backgroundSeconds)
    }
}
