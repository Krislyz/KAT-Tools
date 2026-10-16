// SPDX-License-Identifier: GPL-3.0-only
// Copyright (C) 2026 Krislyz
using System;
using System.Collections.Generic;
using UnityEditor;
using UnityEngine;

namespace KAT
{
    /// <summary>
    /// The KAT panel: one window with tabs, the way the VRChat SDK has its control panel. The assemblies that need the VRChat
    /// SDK add their buttons with <see cref="Register"/>, so this one never depends on it.
    /// </summary>
    public class KatPanel : EditorWindow
    {
        static readonly string[] Tabs = { "Avatar", "Sync", "Optimize", "World", "Settings" };
        const string SiteUrl = "https://krislyz.com/kat/";
        const string RepoUrl = "https://github.com/Krislyz/KAT-Tools";

        class Entry
        {
            public string tab, label, help;
            public Func<bool> canRun;
            public Action run;
        }

        static readonly List<Entry> Entries = new List<Entry>();
        int tab;
        Vector2 scroll;
        GameObject avatar;
        bool useSelection = true;

        /// <summary>Add a button to a tab. Call it again with the same tab and label to replace it.</summary>
        public static void Register(string tab, string label, string help, Func<bool> canRun, Action run)
        {
            Entries.RemoveAll(e => e.tab == tab && e.label == label);
            Entries.Add(new Entry { tab = tab, label = label, help = help, canRun = canRun, run = run });
            if (HasOpenInstances<KatPanel>())
                GetWindow<KatPanel>(false, null, false).Repaint();
        }

        [MenuItem("KAT/KAT Panel")]
        public static void Open()
        {
            var window = GetWindow<KatPanel>("KAT");
            window.minSize = new Vector2(360, 420);
            window.Show();
        }

        static string Version()
        {
            var info = UnityEditor.PackageManager.PackageInfo.FindForAssembly(typeof(KatPanel).Assembly);
            return info != null ? info.version : "";
        }

        void OnSelectionChange()
        {
            if (useSelection && Selection.activeGameObject != null && !EditorUtility.IsPersistent(Selection.activeGameObject))
                avatar = Selection.activeGameObject;
            Repaint();
        }

        void OnGUI()
        {
            DrawHeader();
            DrawAvatarField();
            tab = GUILayout.Toolbar(tab, Tabs, GUILayout.Height(24));
            EditorGUILayout.Space(4);
            scroll = EditorGUILayout.BeginScrollView(scroll);
            switch (Tabs[tab])
            {
                case "Avatar":
                    DrawEntries("Avatar");
                    if (!HasEntry("Avatar"))
                        EditorGUILayout.HelpBox("The avatar tools need the VRChat Avatars SDK. Add it to this project in VCC and they show up here.", MessageType.Info);
                    break;
                case "Sync":
                    DrawSync();
                    break;
                case "Optimize":
                    DrawOptimize();
                    break;
                case "World":
                    DrawWorld();
                    break;
                default:
                    DrawSettings();
                    break;
            }
            EditorGUILayout.EndScrollView();
        }

        void DrawHeader()
        {
            Rect r = GUILayoutUtility.GetRect(0, 54, GUILayout.ExpandWidth(true));
            EditorGUI.DrawRect(r, new Color(0.13f, 0.3f, 0.55f));
            var big = new GUIStyle(EditorStyles.boldLabel) { fontSize = 26, alignment = TextAnchor.MiddleCenter };
            big.normal.textColor = Color.white;
            GUI.Label(new Rect(r.x, r.y, r.width, 36), "KAT", big);
            var small = new GUIStyle(EditorStyles.label) { alignment = TextAnchor.MiddleCenter };
            small.normal.textColor = new Color(0.85f, 0.92f, 1f);
            string v = Version();
            GUI.Label(new Rect(r.x, r.y + 32, r.width, 18), "Krislyz's Avatar Tools" + (v.Length > 0 ? "  " + v : ""), small);
            EditorGUILayout.Space(2);
        }

        void DrawAvatarField()
        {
            using (new EditorGUILayout.HorizontalScope())
            {
                EditorGUI.BeginChangeCheck();
                avatar = (GameObject)EditorGUILayout.ObjectField("Avatar", avatar, typeof(GameObject), true);
                if (EditorGUI.EndChangeCheck() && avatar != null)
                    Selection.activeGameObject = avatar;
                if (GUILayout.Button("Use selected", GUILayout.Width(90)) && Selection.activeGameObject != null)
                    avatar = Selection.activeGameObject;
            }
            useSelection = EditorGUILayout.ToggleLeft("Follow what I select in the scene", useSelection);
            if (avatar != null && Selection.activeGameObject != avatar && !EditorUtility.IsPersistent(avatar))
                Selection.activeGameObject = avatar; // the setup buttons work on the selection
            EditorGUILayout.Space(2);
        }

        bool HasEntry(string t) { return Entries.Exists(e => e.tab == t); }

        void DrawEntries(string t)
        {
            foreach (Entry e in Entries)
                if (e.tab == t)
                    DrawButton(e.label, e.help, e.canRun == null || e.canRun(), e.run);
        }

        static void DrawButton(string label, string help, bool enabled, Action run)
        {
            using (new EditorGUILayout.VerticalScope(EditorStyles.helpBox))
            {
                using (new EditorGUI.DisabledScope(!enabled))
                {
                    if (GUILayout.Button(label, GUILayout.Height(24)))
                        run();
                }
                if (!string.IsNullOrEmpty(help))
                    EditorGUILayout.LabelField(help, EditorStyles.wordWrappedMiniLabel);
            }
        }

        void DrawSync()
        {
            EditorGUILayout.LabelField("Blender and Unity", EditorStyles.boldLabel);
            DrawButton("Send selection to Blender",
                "Writes the materials and textures of the selected model, prefab or object next to the model file, so Blender shows it as it looks here. Right-click works too: KAT > Send to Blender.",
                KatSyncPostprocessor.SendCheck(), KatSyncPostprocessor.SendMenu);
            DrawButton("Write sync files for all models",
                "Updates the sync file of every model that already has a .kat.json.", true, KatSyncPostprocessor.WriteMenu);
            DrawButton("Make materials from Blender's textures",
                "Select model files (.fbx with a .kat.json) first.", Selection.objects.Length > 0, KatSyncPostprocessor.ApplyMenu);
            EditorGUILayout.Space(4);
            EditorGUI.BeginChangeCheck();
            bool auto = EditorGUILayout.ToggleLeft("Make materials automatically when a model is imported", KatSyncPostprocessor.Auto);
            bool write = EditorGUILayout.ToggleLeft("Write the sync files for Blender automatically", KatSyncPostprocessor.AutoWrite);
            if (EditorGUI.EndChangeCheck())
            {
                KatSyncPostprocessor.Auto = auto;
                KatSyncPostprocessor.AutoWrite = write;
            }
            DrawEntries("Sync");
        }

        void DrawOptimize()
        {
            DrawButton("Optimize textures of selection",
                "Shrinks the selected textures (and those of selected objects, materials or folders) to at most 2048 px, with compression, without touching the files.",
                KatTextureOptimizer.CanRun(), KatTextureOptimizer.Run);
            DrawEntries("Optimize");
        }

        void DrawWorld()
        {
            DrawButton("Make selection static", "Marks the selected objects static for baking and batching.",
                Selection.gameObjects.Length > 0, KatWorldPostprocessor.MakeSelectionStatic);
            DrawButton("Bake occlusion culling", "Mark the scenery static first.", true, KatWorldPostprocessor.BakeOcclusion);
            DrawButton("Report scene cost", "Renderers, triangles and materials in the open scene, in the console.", true, KatWorldPostprocessor.Report);
            DrawEntries("World");
        }

        void DrawSettings()
        {
            EditorGUILayout.LabelField("KAT " + Version(), EditorStyles.boldLabel);
            EditorGUILayout.LabelField("Made by Krislyz. GPL-3.0-only. Made to make your avatar workflow easier.", EditorStyles.wordWrappedLabel);
            EditorGUILayout.Space(4);
            if (GUILayout.Button("Website")) Application.OpenURL(SiteUrl);
            if (GUILayout.Button("Source code")) Application.OpenURL(RepoUrl);
            if (GUILayout.Button("Report a problem")) Application.OpenURL(RepoUrl + "/issues");
            DrawEntries("Settings");
        }
    }
}
