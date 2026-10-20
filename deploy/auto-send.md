# Send each new build to the bot automatically

When a release build finishes, the build uploads the APK to the agent on Render. The bot posts it in your chat and asks **Approve and send** or **Cancel**. Nothing reaches the tablets until you tap Approve.

## 1. Get the upload key
In Render, open the **fca-telegram-agent** service → **Environment**. `UPLOAD_KEY` is filled in automatically after the Blueprint syncs. Copy it, and also copy the service address (`https://….onrender.com`) from the top of the page.

## 2. Tell Gradle where to send it
Add these lines to `gradle.properties` in your **user** Gradle folder (`C:\Users\<you>\.gradle\gradle.properties`), not the project's, so the key never goes into git:

```properties
fcaAgentUrl=https://fca-telegram-agent.onrender.com
fcaAgentKey=<the UPLOAD_KEY from Render>
```

## 3. Add the task to the app
Paste this at the end of `app/build.gradle.kts`. Change `FcaSoft` if the app should appear under another name.

```kotlin
// Sends every release APK to the Fca Soft Telegram Agent, which asks for approval before the tablets get it.
val fcaAgentUrl = providers.gradleProperty("fcaAgentUrl").orNull
val fcaAgentKey = providers.gradleProperty("fcaAgentKey").orNull

val sendToFcaAgent by tasks.registering {
    description = "Uploads the release APK to the Fca Soft Telegram Agent for approval"
    onlyIf { fcaAgentUrl != null && fcaAgentKey != null }
    doLast {
        val apk = layout.buildDirectory.dir("outputs/apk/release").get().asFile
            .listFiles { f -> f.extension == "apk" }?.maxByOrNull { it.lastModified() }
            ?: error("No release APK found")
        val name = "FcaSoft-v${android.defaultConfig.versionName}.apk"
        val notes = providers.gradleProperty("releaseNotes").getOrElse("")
        val boundary = "----fca" + System.currentTimeMillis()
        fun field(key: String, value: String) =
            "--$boundary\r\nContent-Disposition: form-data; name=\"$key\"\r\n\r\n$value\r\n".toByteArray()
        val body = java.io.ByteArrayOutputStream().apply {
            write(field("name", name))
            write(field("notes", notes))
            write(("--$boundary\r\nContent-Disposition: form-data; name=\"file\"; filename=\"$name\"\r\n" +
                "Content-Type: application/vnd.android.package-archive\r\n\r\n").toByteArray())
            write(apk.readBytes())
            write("\r\n--$boundary--\r\n".toByteArray())
        }.toByteArray()
        val request = java.net.http.HttpRequest.newBuilder(java.net.URI("$fcaAgentUrl/upload"))
            .header("Authorization", "Bearer $fcaAgentKey")
            .header("Content-Type", "multipart/form-data; boundary=$boundary")
            .timeout(java.time.Duration.ofMinutes(3)) // a sleeping Render service takes up to a minute to wake
            .POST(java.net.http.HttpRequest.BodyPublishers.ofByteArray(body))
            .build()
        val response = java.net.http.HttpClient.newHttpClient()
            .send(request, java.net.http.HttpResponse.BodyHandlers.ofString())
        if (response.statusCode() != 200) error("Fca agent refused the APK: ${response.body()}")
        println("Sent $name to Telegram for approval")
    }
}
tasks.matching { it.name == "assembleRelease" }.configureEach { finalizedBy(sendToFcaAgent) }
```

Then **Build → Build APK(s)** for the release variant (or run `gradlew assembleRelease`). A minute later the bot asks you to approve it.

To add release notes from the command line: `gradlew assembleRelease -PreleaseNotes="New loan calculator"`.

## Rules the bot applies
- The version comes from `versionName`, so raise it for every release. A version that was already sent to the tablets is refused.
- Telegram lets bots upload files up to **50 MB**. A bigger APK has to be sent to the bot by hand.
- Without `fcaAgentUrl`/`fcaAgentKey` the task is skipped, so builds on other computers aren't affected.

## Any other build tool
Anything that can make an HTTP request works, for example:

```
curl -H "Authorization: Bearer <UPLOAD_KEY>" -F "file=@app-release.apk" -F "name=FcaSoft-v1.5.0.apk" -F "notes=New loan calculator" https://fca-telegram-agent.onrender.com/upload
```
