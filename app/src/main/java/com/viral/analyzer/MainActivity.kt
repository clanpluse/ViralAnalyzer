package com.viral.analyzer

import android.Manifest
import android.app.Activity
import android.content.ContentValues
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.MediaStore
import android.provider.Settings
import android.text.SpannableString
import android.text.Spanned
import android.text.style.AbsoluteSizeSpan
import android.text.style.BackgroundColorSpan
import android.text.style.ForegroundColorSpan
import android.view.View
import android.widget.*
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.media3.common.MediaItem
import androidx.media3.common.util.UnstableApi
import androidx.media3.effect.OverlayEffect
import androidx.media3.effect.OverlaySettings
import androidx.media3.effect.TextOverlay
import androidx.media3.effect.TextureOverlay
import androidx.media3.transformer.Composition
import androidx.media3.transformer.EditedMediaItem
import androidx.media3.transformer.Effects
import androidx.media3.transformer.ExportException
import androidx.media3.transformer.ExportResult
import androidx.media3.transformer.Transformer
import com.google.common.collect.ImmutableList
import kotlinx.coroutines.*
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.asRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import kotlin.coroutines.resume
import kotlin.coroutines.suspendCoroutine

class MainActivity : AppCompatActivity() {

    companion object {
        private const val PICK_VIDEO = 1001
        private const val SERVER_URL = BuildConfig.SERVER_URL
    }

    private lateinit var btnPick: Button
    private lateinit var btnAnalyze: Button
    private lateinit var btnEnhance: Button
    private lateinit var btnReport: Button
    private lateinit var tvStatus: TextView
    private lateinit var spinnerNiche: Spinner
    private lateinit var progressBar: ProgressBar
    private lateinit var layoutResult: ScrollView
    private lateinit var tvScore: TextView
    private lateinit var tvHook: TextView
    private lateinit var tvDuration: TextView
    private lateinit var tvStrengths: TextView
    private lateinit var tvImprovements: TextView
    private lateinit var tvCaption: TextView
    private lateinit var tvHashtags: TextView
    private lateinit var tvBestTime: TextView
    private lateinit var tvVerdict: TextView
    private lateinit var tvTranscript: TextView
    private lateinit var tvVideoName: TextView
    private lateinit var btnCopyAll: Button
    private lateinit var etReferenceUrl: EditText
    private lateinit var btnAnalyzeRef: Button

    private var selectedVideoUri: Uri? = null
    private var lastAnalysisResult: JSONObject? = null
    private val client = OkHttpClient.Builder()
        .connectTimeout(30, java.util.concurrent.TimeUnit.SECONDS)
        .readTimeout(300, java.util.concurrent.TimeUnit.SECONDS)
        .writeTimeout(120, java.util.concurrent.TimeUnit.SECONDS)
        .build()

    private val requestPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        btnPick = findViewById(R.id.btnPick)
        btnAnalyze = findViewById(R.id.btnAnalyze)
        btnEnhance = findViewById(R.id.btnEnhance)
        btnReport = findViewById(R.id.btnReport)
        tvStatus = findViewById(R.id.tvStatus)
        spinnerNiche = findViewById(R.id.spinnerNiche)
        progressBar = findViewById(R.id.progressBar)
        layoutResult = findViewById(R.id.layoutResult)
        tvScore = findViewById(R.id.tvScore)
        tvHook = findViewById(R.id.tvHook)
        tvDuration = findViewById(R.id.tvDuration)
        tvStrengths = findViewById(R.id.tvStrengths)
        tvImprovements = findViewById(R.id.tvImprovements)
        tvCaption = findViewById(R.id.tvCaption)
        tvHashtags = findViewById(R.id.tvHashtags)
        tvBestTime = findViewById(R.id.tvBestTime)
        tvVerdict = findViewById(R.id.tvVerdict)
        tvTranscript = findViewById(R.id.tvTranscript)
        tvVideoName = findViewById(R.id.tvVideoName)
        btnCopyAll = findViewById(R.id.btnCopyAll)
        etReferenceUrl = findViewById(R.id.etReferenceUrl)
        btnAnalyzeRef = findViewById(R.id.btnAnalyzeRef)

        val niches = listOf(
            "تصاميم منزلية وديكور",
            "تسويق منتجات"
        )
        spinnerNiche.adapter = ArrayAdapter(
            this, android.R.layout.simple_spinner_dropdown_item, niches
        )

        requestPermissions()

        btnPick.setOnClickListener { pickVideo() }
        btnAnalyze.setOnClickListener { analyzeVideo() }
        btnEnhance.setOnClickListener { enhanceVideo() }
        btnReport.setOnClickListener { showTrendReport() }
        btnAnalyzeRef.setOnClickListener { analyzeReferenceVideo() }
        btnCopyAll.setOnClickListener { copyAllToClipboard() }
    }

    private fun requestPermissions() {
        val perms = mutableListOf<String>()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            perms.add(Manifest.permission.READ_MEDIA_VIDEO)
            perms.add(Manifest.permission.POST_NOTIFICATIONS)
        } else {
            perms.add(Manifest.permission.READ_EXTERNAL_STORAGE)
            perms.add(Manifest.permission.WRITE_EXTERNAL_STORAGE)
        }
        requestPermissionLauncher.launch(perms.toTypedArray())
    }

    private fun pickVideo() {
        val intent = Intent(Intent.ACTION_PICK, MediaStore.Video.Media.EXTERNAL_CONTENT_URI)
        startActivityForResult(intent, PICK_VIDEO)
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == PICK_VIDEO && resultCode == Activity.RESULT_OK) {
            selectedVideoUri = data?.data
            selectedVideoUri?.let { uri ->
                val name = getFileName(uri)
                tvVideoName.text = "📹 $name"
                tvVideoName.visibility = View.VISIBLE
                btnAnalyze.isEnabled = true
                tvStatus.text = "جاهز للتحليل"
                layoutResult.visibility = View.GONE
                btnEnhance.visibility = View.GONE
            }
        }
    }

    private fun getFileName(uri: Uri): String {
        val cursor = contentResolver.query(uri, null, null, null, null)
        return cursor?.use {
            val nameIndex = it.getColumnIndex(MediaStore.Video.Media.DISPLAY_NAME)
            it.moveToFirst()
            it.getString(nameIndex)
        } ?: "فيديو"
    }

    private fun analyzeVideo() {
        val uri = selectedVideoUri ?: return
        val niche = spinnerNiche.selectedItem.toString()

        btnAnalyze.isEnabled = false
        btnEnhance.visibility = View.GONE
        progressBar.visibility = View.VISIBLE
        layoutResult.visibility = View.GONE
        tvStatus.text = "⏳ جارٍ التحليل... (قد يستغرق دقيقة)"

        CoroutineScope(Dispatchers.IO).launch {
            try {
                val tmpFile = File(cacheDir, "upload_video.mp4")
                contentResolver.openInputStream(uri)?.use { input ->
                    FileOutputStream(tmpFile).use { output -> input.copyTo(output) }
                }

                val requestBody = MultipartBody.Builder()
                    .setType(MultipartBody.FORM)
                    .addFormDataPart("video", "video.mp4",
                        tmpFile.asRequestBody("video/mp4".toMediaType()))
                    .addFormDataPart("niche", niche)
                    .build()

                val request = Request.Builder()
                    .url("$SERVER_URL/analyze")
                    .post(requestBody)
                    .build()

                val response = client.newCall(request).execute()
                val body = response.body?.string() ?: throw Exception("لا يوجد رد")
                if (!response.isSuccessful) throw Exception("خطأ: ${response.code}")

                val json = JSONObject(body)
                lastAnalysisResult = json
                tmpFile.delete()

                withContext(Dispatchers.Main) {
                    displayResult(json)
                    btnEnhance.visibility = View.VISIBLE
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    tvStatus.text = "❌ خطأ: ${e.message}"
                    progressBar.visibility = View.GONE
                    btnAnalyze.isEnabled = true
                }
            }
        }
    }

    private fun enhanceVideo() {
        val uri = selectedVideoUri ?: return
        val niche = spinnerNiche.selectedItem.toString()
        val result = lastAnalysisResult ?: return

        btnEnhance.isEnabled = false
        progressBar.visibility = View.VISIBLE
        tvStatus.text = "🎬 جارٍ توليد النصوص..."

        CoroutineScope(Dispatchers.IO).launch {
            try {
                // 1) Ask the server only for the smart text (fast, lightweight JSON)
                val durationSec = result.optInt("duration", 0)
                val requestBody = MultipartBody.Builder()
                    .setType(MultipartBody.FORM)
                    .addFormDataPart("niche", niche)
                    .addFormDataPart("title", result.optString("caption", ""))
                    .addFormDataPart("transcript", result.optString("transcript", ""))
                    .addFormDataPart("duration", durationSec.toString())
                    .build()

                val request = Request.Builder()
                    .url("$SERVER_URL/enhance")
                    .post(requestBody)
                    .build()

                val resp = client.newCall(request).execute()
                val body = resp.body?.string() ?: throw Exception("لا يوجد رد")
                if (!resp.isSuccessful) throw Exception("خطأ: ${resp.code}")
                val json = JSONObject(body)

                // Dynamic overlays: count, timing and position decided by the analysis
                val overlaysJson = json.optJSONArray("overlays") ?: JSONArray()
                val segments = ArrayList<OverlaySegment>()
                for (i in 0 until overlaysJson.length()) {
                    val o = overlaysJson.getJSONObject(i)
                    val text = o.optString("text", "").trim()
                    if (text.isEmpty()) continue
                    segments.add(
                        OverlaySegment(
                            text = text,
                            startPct = o.optDouble("start_pct", 0.0).toFloat(),
                            endPct = o.optDouble("end_pct", 1.0).toFloat(),
                            position = o.optString("position", "top"),
                            purpose = o.optString("purpose", "")
                        )
                    )
                }
                if (segments.isEmpty()) throw Exception("لم تصل نصوص للتحسين")
                val algoBoost = json.optString("algorithm_score_boost", "")

                // 2) Render the overlays ON THE DEVICE (fast, hardware accelerated)
                withContext(Dispatchers.Main) {
                    tvStatus.text = "🎬 جارٍ تحسين الفيديو على الجهاز..."
                }
                val outFile = File(cacheDir, "viral_enhanced_${System.currentTimeMillis()}.mp4")
                renderOverlaysOnDevice(uri, segments, durationSec, outFile)

                // 3) Add sound effects on the server (audio-only mix, fast)
                var finalFile = outFile
                try {
                    withContext(Dispatchers.Main) {
                        tvStatus.text = "🔊 جارٍ إضافة المؤثرات الصوتية..."
                    }
                    val overlayTimes = JSONArray()
                    for (s in segments) overlayTimes.put((s.startPct * durationSec).toDouble())
                    val sfxFile = addSoundEffects(outFile, niche, durationSec, overlayTimes.toString())
                    if (sfxFile != null) {
                        finalFile = sfxFile
                        outFile.delete()
                    }
                } catch (_: Exception) {
                    // SFX is a bonus; if it fails, keep the text-only video
                }

                // 4) Save to gallery
                saveVideoFileToGallery(finalFile)
                finalFile.delete()

                withContext(Dispatchers.Main) {
                    progressBar.visibility = View.GONE
                    btnEnhance.isEnabled = true
                    tvStatus.text = "✅ الفيديو المحسّن تم حفظه في المعرض!"

                    val report = buildString {
                        appendLine("🎬 التحسينات المُطبَّقة (${segments.size} نصوص):")
                        appendLine("━━━━━━━━━━━━━━━━━")
                        segments.forEachIndexed { idx, s ->
                            val from = (s.startPct * durationSec).toInt()
                            val to = (s.endPct * durationSec).toInt()
                            val label = if (s.purpose.isNotEmpty()) s.purpose else "نص ${idx + 1}"
                            appendLine("\n• $label (${from}-${to}ث):")
                            appendLine("  \"${s.text}\"")
                        }
                        if (algoBoost.isNotEmpty()) {
                            appendLine("\n🚀 تأثير على الخوارزمية:")
                            appendLine("  $algoBoost")
                        }
                    }

                    android.app.AlertDialog.Builder(this@MainActivity)
                        .setTitle("✅ الفيديو المحسّن جاهز!")
                        .setMessage(report)
                        .setPositiveButton("ممتاز!") { _, _ -> }
                        .show()
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    progressBar.visibility = View.GONE
                    btnEnhance.isEnabled = true
                    tvStatus.text = "❌ خطأ في التحسين: ${e.message}"
                }
            }
        }
    }

    /** Analyze a viral reference video URL so future enhancements mimic its winning formula. */
    private fun analyzeReferenceVideo() {
        val url = etReferenceUrl.text.toString().trim()
        if (url.isEmpty()) {
            Toast.makeText(this, "الصق رابط فيديو أولاً", Toast.LENGTH_SHORT).show()
            return
        }
        val niche = spinnerNiche.selectedItem.toString()
        btnAnalyzeRef.isEnabled = false
        progressBar.visibility = View.VISIBLE
        tvStatus.text = "📈 جارٍ تحليل الفيديو المرجعي..."

        CoroutineScope(Dispatchers.IO).launch {
            try {
                val reqBody = MultipartBody.Builder()
                    .setType(MultipartBody.FORM)
                    .addFormDataPart("url", url)
                    .addFormDataPart("niche", niche)
                    .build()
                val submit = client.newCall(
                    Request.Builder().url("$SERVER_URL/analyze-reference").post(reqBody).build()
                ).execute()
                val sBody = submit.body?.string() ?: throw Exception("لا يوجد رد")
                if (!submit.isSuccessful) throw Exception("خطأ: ${submit.code}")
                val jobId = JSONObject(sBody).optString("job_id", "")
                if (jobId.isEmpty()) throw Exception("تعذّر بدء التحليل")

                var profile: JSONObject? = null
                var attempt = 0
                while (attempt < 90) {
                    attempt++
                    val pr = client.newCall(
                        Request.Builder().url("$SERVER_URL/reference-result/$jobId").get().build()
                    ).execute()
                    val pBody = pr.body?.string() ?: ""
                    when (pr.code) {
                        200 -> profile = JSONObject(pBody).optJSONObject("profile")
                        202 -> {
                            withContext(Dispatchers.Main) {
                                tvStatus.text = "📈 جارٍ تحليل الفيديو المرجعي... (${attempt * 4} ثانية)"
                            }
                            kotlinx.coroutines.delay(4000); continue
                        }
                        else -> throw Exception("فشل التحليل: $pBody")
                    }
                    break
                }
                if (profile == null) throw Exception("انتهت المهلة")

                val whyViral = profile.optString("why_viral", "")
                val summary = profile.optString("summary", "")
                val texts = profile.optJSONArray("onscreen_texts") ?: JSONArray()
                val onscreen = buildString {
                    for (i in 0 until texts.length()) append("• ${texts.getString(i)}\n")
                }.trim()

                withContext(Dispatchers.Main) {
                    progressBar.visibility = View.GONE
                    btnAnalyzeRef.isEnabled = true
                    tvStatus.text = "✅ تم تعلّم وصفة الفيديو المرجعي!"
                    val msg = buildString {
                        appendLine("📈 لماذا نجح هذا الفيديو:")
                        appendLine(whyViral)
                        if (onscreen.isNotEmpty()) {
                            appendLine("\n📝 النصوص التي ظهرت عليه:")
                            appendLine(onscreen)
                        }
                        if (summary.isNotEmpty()) {
                            appendLine("\n🏆 الوصفة الفائزة:")
                            appendLine(summary)
                        }
                        appendLine("\n✅ سيُطبّق هذا الأسلوب تلقائياً عند تحسين فيديوهاتك في هذا المجال.")
                    }
                    android.app.AlertDialog.Builder(this@MainActivity)
                        .setTitle("✅ تم تحليل الفيديو المرجعي")
                        .setMessage(msg)
                        .setPositiveButton("ممتاز!") { _, _ -> }
                        .show()
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    progressBar.visibility = View.GONE
                    btnAnalyzeRef.isEnabled = true
                    tvStatus.text = "❌ خطأ في تحليل المرجع: ${e.message}"
                }
            }
        }
    }

    /** Burn the dynamic list of text overlays onto the video using Media3 Transformer. */
    @OptIn(UnstableApi::class)
    private suspend fun renderOverlaysOnDevice(
        inputUri: Uri,
        segments: List<OverlaySegment>,
        durationSec: Int,
        outFile: File
    ) = suspendCoroutine<Unit> { cont ->
        val durationUs = if (durationSec > 0) durationSec * 1_000_000L else 30_000_000L

        // Font size proportional to the video height -> consistent look on any resolution
        var videoHeight = 1280
        try {
            val mmr = android.media.MediaMetadataRetriever()
            mmr.setDataSource(this, inputUri)
            videoHeight = mmr.extractMetadata(
                android.media.MediaMetadataRetriever.METADATA_KEY_VIDEO_HEIGHT
            )?.toIntOrNull() ?: 1280
            mmr.release()
        } catch (_: Exception) {}
        val fontPx = (videoHeight * 0.04f).toInt().coerceIn(22, 64)

        // One overlay per segment, each visible only inside its own time window.
        val overlays = ArrayList<TextureOverlay>()
        for (s in segments) {
            val startUs = (s.startPct.coerceIn(0f, 1f) * durationUs).toLong()
            var endUs = (s.endPct.coerceIn(0f, 1f) * durationUs).toLong()
            if (endUs <= startUs) endUs = (startUs + 2_000_000L).coerceAtMost(durationUs)
            val anchorY = when (s.position) {
                "bottom" -> -0.78f
                "center" -> 0.0f
                else -> 0.78f
            }
            overlays.add(WindowedTextOverlay(s.text, startUs, endUs, anchorY, fontPx))
        }

        if (overlays.isEmpty()) {
            cont.resumeWith(Result.failure(Exception("لا توجد نصوص لإضافتها")))
            return@suspendCoroutine
        }

        val overlayEffect = OverlayEffect(ImmutableList.copyOf(overlays))

        val editedItem = EditedMediaItem.Builder(MediaItem.fromUri(inputUri))
            .setEffects(Effects(emptyList(), listOf(overlayEffect)))
            .build()

        val transformer = Transformer.Builder(this)
            .addListener(object : Transformer.Listener {
                override fun onCompleted(composition: Composition, result: ExportResult) {
                    cont.resume(Unit)
                }
                override fun onError(
                    composition: Composition,
                    result: ExportResult,
                    exception: ExportException
                ) {
                    cont.resumeWith(Result.failure(Exception("فشل المعالجة: ${exception.message}")))
                }
            })
            .build()

        // Transformer must be started on the main thread
        runOnUiThread {
            transformer.start(editedItem, outFile.absolutePath)
        }
    }

    /** Upload the (text-rendered) video and get it back with sound effects mixed in. */
    private suspend fun addSoundEffects(
        src: File, niche: String, durationSec: Int, overlayTimesJson: String
    ): File? {
        val reqBody = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart("video", "v.mp4", src.asRequestBody("video/mp4".toMediaType()))
            .addFormDataPart("niche", niche)
            .addFormDataPart("duration", durationSec.toString())
            .addFormDataPart("overlay_times", overlayTimesJson)
            .build()
        val submit = client.newCall(
            Request.Builder().url("$SERVER_URL/enhance-audio").post(reqBody).build()
        ).execute()
        val sBody = submit.body?.string() ?: return null
        if (!submit.isSuccessful) return null
        val jobId = JSONObject(sBody).optString("job_id", "")
        if (jobId.isEmpty()) return null

        var attempt = 0
        while (attempt < 60) {
            attempt++
            val pr = client.newCall(
                Request.Builder().url("$SERVER_URL/audio-result/$jobId").get().build()
            ).execute()
            when (pr.code) {
                200 -> {
                    val bytes = pr.body?.bytes() ?: return null
                    val out = File(cacheDir, "viral_sfx_${System.currentTimeMillis()}.mp4")
                    out.writeBytes(bytes)
                    return out
                }
                202 -> {
                    pr.close()
                    withContext(Dispatchers.Main) {
                        tvStatus.text = "🔊 جارٍ إضافة المؤثرات... (${attempt * 2} ثانية)"
                    }
                    kotlinx.coroutines.delay(2000); continue
                }
                else -> return null
            }
        }
        return null
    }

    private fun saveVideoFileToGallery(src: File) {
        val filename = "viral_enhanced_${System.currentTimeMillis()}.mp4"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val values = ContentValues().apply {
                put(MediaStore.Video.Media.DISPLAY_NAME, filename)
                put(MediaStore.Video.Media.MIME_TYPE, "video/mp4")
                put(MediaStore.Video.Media.RELATIVE_PATH, Environment.DIRECTORY_MOVIES)
            }
            val uri = contentResolver.insert(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, values)
            uri?.let {
                contentResolver.openOutputStream(it)?.use { out ->
                    src.inputStream().use { input -> input.copyTo(out) }
                }
            }
        } else {
            val dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_MOVIES)
            dir.mkdirs()
            src.copyTo(File(dir, filename), overwrite = true)
        }
    }

    private fun saveVideoToGallery(bytes: ByteArray) {
        val filename = "viral_enhanced_${System.currentTimeMillis()}.mp4"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val values = ContentValues().apply {
                put(MediaStore.Video.Media.DISPLAY_NAME, filename)
                put(MediaStore.Video.Media.MIME_TYPE, "video/mp4")
                put(MediaStore.Video.Media.RELATIVE_PATH, Environment.DIRECTORY_MOVIES)
            }
            val uri = contentResolver.insert(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, values)
            uri?.let {
                contentResolver.openOutputStream(it)?.use { out -> out.write(bytes) }
            }
        } else {
            val dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_MOVIES)
            dir.mkdirs()
            File(dir, filename).writeBytes(bytes)
        }
    }

    private fun showTrendReport() {
        btnReport.isEnabled = false
        tvStatus.text = "📊 جارٍ تحميل تقرير الترند..."

        CoroutineScope(Dispatchers.IO).launch {
            try {
                val request = Request.Builder()
                    .url("$SERVER_URL/trend-report")
                    .get()
                    .build()

                val response = client.newCall(request).execute()
                val body = response.body?.string() ?: ""

                withContext(Dispatchers.Main) {
                    btnReport.isEnabled = true
                    if (response.isSuccessful) {
                        showReportDialog(body)
                    } else {
                        tvStatus.text = "⏳ لم يتم إنشاء التقرير بعد، انتظر التحليل اليومي"
                    }
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    btnReport.isEnabled = true
                    tvStatus.text = "❌ خطأ: ${e.message}"
                }
            }
        }
    }

    private fun showReportDialog(reportJson: String) {
        try {
            val json = JSONObject(reportJson)
            val niches = json.optJSONObject("niches") ?: return
            val generatedAt = json.optString("generated_at", "").take(10)

            val sb = StringBuilder()
            sb.appendLine("📊 تقرير الترند - $generatedAt")
            sb.appendLine("━━━━━━━━━━━━━━━━━")

            niches.keys().forEach { niche ->
                val data = niches.getJSONObject(niche)
                sb.appendLine("\n🔥 $niche")
                sb.appendLine("📹 فيديوهات محللة: ${data.optInt("total_videos")}")
                sb.appendLine("⏱ المدة المثالية: ${data.optInt("optimal_duration")}ث")
                val hashtags = data.optJSONArray("top_hashtags")
                if (hashtags != null && hashtags.length() > 0) {
                    val tags = (0 until hashtags.length()).map { "#${hashtags.getString(it)}" }
                    sb.appendLine("# ${tags.joinToString(" ")}")
                }
                sb.appendLine("💡 ${data.optString("key_finding")}")

                val accounts = data.optJSONArray("accounts")
                if (accounts != null) {
                    sb.appendLine("الحسابات المدروسة:")
                    for (i in 0 until accounts.length()) {
                        val acc = accounts.getJSONObject(i)
                        sb.appendLine("  • @${acc.optString("username")} (${acc.optInt("videos_analyzed")} فيديو)")
                    }
                }
            }

            android.app.AlertDialog.Builder(this)
                .setTitle("📊 تقرير الترند")
                .setMessage(sb.toString())
                .setPositiveButton("نسخ") { _, _ ->
                    val clipboard = getSystemService(android.content.ClipboardManager::class.java)
                    clipboard.setPrimaryClip(android.content.ClipData.newPlainText("report", sb.toString()))
                    Toast.makeText(this, "تم النسخ", Toast.LENGTH_SHORT).show()
                }
                .setNegativeButton("إغلاق", null)
                .show()

            tvStatus.text = "✅ تقرير الترند"
        } catch (e: Exception) {
            tvStatus.text = "❌ خطأ في قراءة التقرير"
        }
    }

    private fun copyAllToClipboard() {
        val result = lastAnalysisResult ?: return
        val text = buildString {
            appendLine(result.optString("caption", ""))
            appendLine()
            appendLine(result.optString("hashtags", ""))
            appendLine()
            appendLine("⏰ أفضل وقت: ${result.optString("best_time", "")}")
        }
        val clipboard = getSystemService(android.content.ClipboardManager::class.java)
        clipboard.setPrimaryClip(android.content.ClipData.newPlainText("post", text))
        Toast.makeText(this, "✅ تم نسخ الكابشن والهاشتاقات", Toast.LENGTH_SHORT).show()
    }

    private fun displayResult(json: JSONObject) {
        progressBar.visibility = View.GONE
        layoutResult.visibility = View.VISIBLE
        btnAnalyze.isEnabled = true

        val score = json.optInt("score", 0)
        tvScore.text = "$score / 100"
        tvScore.setTextColor(when {
            score >= 75 -> getColor(android.R.color.holo_green_dark)
            score >= 50 -> getColor(android.R.color.holo_orange_dark)
            else -> getColor(android.R.color.holo_red_dark)
        })

        tvHook.text = json.optString("hook_rating", "-")
        tvDuration.text = "${json.optInt("duration")}ث • ${json.optString("duration_rating")}"
        tvBestTime.text = json.optString("best_time", "-")
        tvVerdict.text = json.optString("verdict", "-")
        tvCaption.text = json.optString("caption", "-")
        tvHashtags.text = json.optString("hashtags", "-")
        tvTranscript.text = json.optString("transcript", "-")

        val strengths = json.optJSONArray("strengths") ?: JSONArray()
        tvStrengths.text = buildString {
            for (i in 0 until strengths.length()) append("✅ ${strengths.getString(i)}\n")
        }.trim()

        val improvements = json.optJSONArray("improvements") ?: JSONArray()
        tvImprovements.text = buildString {
            for (i in 0 until improvements.length()) append("⚠️ ${improvements.getString(i)}\n")
        }.trim()

        val trendMatch = json.optInt("trend_match", 0)
        val trendUpdated = json.optString("trend_updated", "")
        tvStatus.text = if (trendMatch > 0)
            "✅ تحليل مكتمل • توافق مع الترند: $trendMatch% ($trendUpdated)"
        else "✅ تحليل مكتمل"
    }
}

/** One dynamic on-screen text segment decided by the server analysis. */
data class OverlaySegment(
    val text: String,
    val startPct: Float,
    val endPct: Float,
    val position: String,
    val purpose: String
)

/**
 * A text overlay shown only within [startUs, endUs]. Outside the window it stays
 * fully transparent. Positioned via [topAnchorY] in normalized coords:
 * +0.75 = near the top, -0.75 = near the bottom.
 */
@UnstableApi
class WindowedTextOverlay(
    text: String,
    private val startUs: Long,
    private val endUs: Long,
    private val topAnchorY: Float,
    fontPx: Int = 48
) : TextOverlay() {

    private val span: SpannableString = SpannableString(text).apply {
        setSpan(AbsoluteSizeSpan(fontPx), 0, length, Spanned.SPAN_INCLUSIVE_INCLUSIVE)
        setSpan(ForegroundColorSpan(Color.WHITE), 0, length, Spanned.SPAN_INCLUSIVE_INCLUSIVE)
        setSpan(BackgroundColorSpan(Color.argb(150, 0, 0, 0)), 0, length, Spanned.SPAN_INCLUSIVE_INCLUSIVE)
    }

    override fun getText(presentationTimeUs: Long): SpannableString = span

    override fun getOverlaySettings(presentationTimeUs: Long): OverlaySettings {
        val visible = presentationTimeUs in startUs..endUs
        return OverlaySettings.Builder()
            .setAlphaScale(if (visible) 1f else 0f)
            .setBackgroundFrameAnchor(0f, topAnchorY)
            .setOverlayFrameAnchor(0f, 0f)
            .build()
    }
}
