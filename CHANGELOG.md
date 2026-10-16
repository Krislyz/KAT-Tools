# Changelog

## 0.3.0 - 2026-10-17

### Added
- Blender: update checker. Each time Blender starts, KAT asks GitHub whether a newer version is out (needs Allow Online Access, can be turned off in Settings). An **Updates** panel shows the result, and **Update now** downloads and installs it. Nothing installs without you pressing it. Restart Blender afterwards.

### Changed
- Release files are named `katt-blender-plugin-vX.X.X.zip` and `katt-unity-plugin-vX.X.X.zip`.
- The Optimize panel starts closed. Only Quick access, Updates and Credits start open.

## 0.2.2 - 2026-10-17

### Added
- Export for Unity has options in the file window. **Textures**: leave them, copy the ones that are not in the Unity project yet, or copy all, into a folder you pick (default `<name>_textures` next to the FBX). **Model**: apply modifiers, triangulate, leaf bones, embed the textures in the FBX, smoothing. **KAT**: whether to write the `.kat.json`. Nothing changes unless you tick it.

### Fixed
- Blender in the background (tests, scripts) no longer runs the history snapshots on a thread. It could still be running at quit and crash Blender.

## 0.2.1 - 2026-10-16

### Updated
- Unity: the KAT panel looks like KAT now: a deep navy banner and a navy list of pages down the left side, instead of a bright blue banner with a row of tabs.

## 0.2.0 - 2026-10-16

### Added
- Unity: the KAT panel. **KAT > KAT Panel** opens one window with tabs (Avatar, Sync, Optimize, World, Settings), like the VRChat SDK control panel. The avatar to work on is picked at the top, or follows your selection. The VRChat tools (Set up VRChat avatar, Set up outfit toggles, VRChat pickup) appear in it when the VRChat SDK is in the project.

### Updated
- Unity: the long KAT menu is gone. Its buttons are in the panel. Right-click **KAT > Send to Blender** stays.

## 0.1.1 - 2026-10-16

### Fixed
- Unity: opening a project with KAT installed logged `'[3.0.0,)' is not a valid expression` and a NullReferenceException from the script compiler. The VRChat avatar and world assemblies used an open-ended version range that Unity does not accept. They use a plain minimum version (`3.0.0`) now.

## 0.1.0 - 2026-10-16

First release.

### Added
- Blender add-on (Blender 5.0 to 5.2, one extension zip) in one KAT tab: import any model file, find missing textures, use the textures Unity uses, flat texture view.
- Fix and clean: bone naming, T-pose, go into pose mode, save as rest pose, clean vertex groups, limit weights.
- Face: visemes (a-b-c order), blink and eye bones, all VRChat Unified face tracking shapes (the missing ones are approximated).
- Outfits: anti-clip shape keys for layered clothes. The skin can be several meshes. Works from the basis shape and tries around 30 poses.
- Optimization: one checklist with Safe, PC Good and Quest presets, bone merging, join and weld.
- Atlas: many meshes at once, with normal maps, emission and cut-out alpha.
- Textures: see what they cost, shrink the big ones without touching the files.
- Reduce triangles: one slider per mesh, hair, face and hands stay put. Optional real-time preview.
- World and props: scan, combine static meshes, collision, LODs, lightmap UVs, prop generators, export for Unity.
- Check against VRChat PC and Quest ranks.
- Version history for imports and exports, with their textures.
- Unity package (VPM, for VCC and ALCOM): humanoid import, materials from Blender's textures, VRChat avatar setup, outfit toggles, texture optimizer, world and prop import.
- Tests for Blender 5.0, 5.1 and 5.2, and CI.
- Unity: when you export a model again, KAT asks once whether to replace the materials it made last time or keep them as they are. Replace is the default, and it rewrites the same material assets, so scenes and prefabs keep their links.
- A README with install gifs for Blender and for VCC, and screenshots of the panels.
- A link to the website in the Credits panel, in the Blender manifest and in the Unity package.
- Refit clothes: put an outfit made for another avatar on yours. Skeleton fit, surface fit, weights and binding to your rig, in one press, with a progress bar. Cloth inside the body is pushed out, tight cloth is drawn on, loose cloth is left loose, and hems and collars come out clean. Trousers and other tight clothes stay smooth at the waist: a hidden cap or pocket bag inside the body is left alone.
- Refit keeps layers apart: every garment is stopped by the ones fitted under it the way the skin stops it, so a jacket does not sit inside a shirt. What was tucked in (a shirt hem in trousers) stays tucked in.
- Refit keeps hard parts hard: shoes, helmets, bags, ties, buckles and badges keep their shape and move as one block, onto the body and over the clothes under them. A belt, a frill or a cuff that is a piece of its own rides along with the cloth under it.
- Refit settles each garment with Blender's own cloth after the fit: stiff, held to the shape it has, colliding with your skin, so a suit stays a suit. Only cloth lying near the skin is settled, linings and loose cloth are held. It never pushes cloth into the body, blendshapes are left alone, props and hard parts are skipped, and a result that comes out torn is smoothed or thrown away. Settle with cloth and Stiffness are in the panel.
- Refit can finish the job: hide carried props, list the clothes in layer order, make the fit shapes and switch them on. Each step is optional.
- Unity: right-click a model, a prefab or an object and pick KAT > Send to Blender. It writes the sync file with the materials and textures as they are on that object, ready for Blender to pick up on import. The sync file also says whether each material is opaque, cutout or transparent, so Blender shows a texture's alpha only where Unity uses it.
- Merge armatures merges what matches: a Hips in both is one Hips, the weights go to the base bone and what hung from it hangs from the base bone. Options: keep or fold the extra bones, apply transforms, join meshes, remove unused bones, clean shape keys. Pick the two armatures in the panel.
- Bone merging: pick one chain (an ear, a tail) or all, send the weights to the parents or to the active bone, keep the merged bones, only touch visible meshes. A Delete box for bones without weights (twist and hidden bones can be kept), constraints and empty vertex groups, and Connect bones.
- Missing textures are looked for automatically when a .blend opens and after Refit, next to where the model came from. The Find missing textures button only asks for a folder when something is still missing.
- Refit has three modes: Fast (coarser, no cloth step, quick poses, for a first look), Intermediate (the everyday one) and Accurate (a finer mesh, many more rounds of pushing out and settling, a check after each garment, and the thorough set of poses for the fit shapes). Accurate takes several times longer. A last pass keeps every outer vertex off the skin in every mode.
- Refit first lays each garment over the body in one smooth movement of space (Fast skips it): every round asks each vertex how far it would have to go to be out of the skin and over the garments under it, and moves the whole garment by a soft field fitted to those requests. Folds, hems, lapels and collars keep the shape they were designed in and only swell where the body is bigger; the single-vertex pushes of the fit that follows are small. Garments whose hem was tucked into another stay tucked.
- Refit lays each garment over the body as one smooth shape: what the fit did to each vertex is kept as a smooth field, and every vertex that would end up inside the skin or the clothes under it is put on the surface and held there while the rest relaxes between them. Hems, cuffs and waistbands come out as smooth as they were designed.
- Refit keeps the original: each garment keeps the shape it had before the fit as a shape key called `KAT.original`. **View original** and **View fit** in the Refit panel switch between the two, and the key can be a toggle in Unity. Untick **Keep the original** to skip it.

### Good to know
- Unity: add `https://cdn.krislyz.com/vpm/index.json` to VCC or ALCOM. More at https://krislyz.com/kat/
- The Unity part is untested in a real project so far.
- A Quest rank is not always reachable: hair, face and hands are never reduced.
- GPL-3.0-only.
