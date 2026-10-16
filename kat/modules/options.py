# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""What you have picked in the panels (options, shape keys, groups). Lives on the scene, so it is saved with the file."""
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, PointerProperty, StringProperty

from ..core import visemes
from . import armature


_chains = [("ALL", "All loose bones", "Thin every chain")]
_replaced = []  # Blender keeps reading the strings of a list after it has been replaced: they must outlive it


def chain_items():
    """What the Chain menu shows. Blender asks for this at odd moments (a file loading, a property being read), so it only
    hands back a list that already exists. The panel refreshes the list when it is drawn."""
    return _chains


def refresh_chains(context):
    """The chains of loose bones of the rig, for the Chain menu."""
    global _chains
    from . import optimize
    rig = armature.rig_of(context)
    items = [("ALL", "All loose bones", "Thin every chain")]
    if rig is not None:
        for top, bones in optimize.merge_chains(rig).items():
            items.append((top, f"{top} ({len(bones)} bones)", ""))
    if items != _chains:
        _replaced.append(_chains)
        del _replaced[:-256]
        _chains = items


def _is_mesh(self, ob):
    return ob.type == "MESH"


def _fill_guesses(self, context):
    """Choosing the face mesh pre-fills the mouth shapes from their usual names."""
    ob = self.face_mesh
    blocks = ob.data.shape_keys.key_blocks if ob is not None and ob.data.shape_keys else None
    if blocks is None:
        return
    names = [b.name for b in blocks]
    for prop, role in (("viseme_a", "a"), ("viseme_o", "o"), ("viseme_ch", "ch"),
                       ("blink_left", "blink_left"), ("blink_right", "blink_right")):
        if getattr(self, prop) not in names:
            setattr(self, prop, visemes.guess(names, role))


class KAT_Options(bpy.types.PropertyGroup):
    # Fix model: only the harmless step is on by default, everything that changes data is up to you
    fix_rename: BoolProperty(name="Name humanoid bones", default=True,
                             description="Rename bones to one humanoid naming style. Meshes follow. Nothing is lost")
    fix_style: EnumProperty(name="Names", items=armature.STYLES, default="unity")
    fix_transforms: BoolProperty(name="Apply rotation and scale", default=False,
                                 description="Bake the rig's and meshes' rotation and scale into their data. The model stays where it is")
    fix_clean_groups: BoolProperty(name="Remove empty vertex groups", default=False,
                                   description="Delete vertex groups that no vertex has any weight in")
    fix_limit_weights: BoolProperty(name="Limit weights to 4", default=False,
                                    description="Keep the 4 strongest bone weights per vertex and normalize them (VRChat skins with 4)")
    fix_drop_non_bone: BoolProperty(name="Remove non-bone groups", default=False,
                                    description="Also delete vertex groups that match no bone. They may be used by something else")

    # Face
    face_mesh: PointerProperty(name="Mesh", type=bpy.types.Object, poll=_is_mesh, update=_fill_guesses,
                               description="The mesh that holds the face shape keys and eye groups")
    viseme_a: StringProperty(name="Viseme AA (open)", description="Shape key for an open mouth, like 'ah'")
    viseme_o: StringProperty(name="Viseme OH (round)", description="Shape key for a rounded mouth, like 'oh'")
    viseme_ch: StringProperty(name="Viseme CH (wide)", description="Shape key for a wide mouth with teeth, like 'ee'")
    viseme_intensity: FloatProperty(name="Mix intensity", default=1.0, min=0.0, max=2.0,
                                    description="How strongly the three shapes are mixed into the 15 visemes")
    blink_left: StringProperty(name="Blink left")
    blink_right: StringProperty(name="Blink right")
    blink_lower_left: StringProperty(name="Lower lid left")
    blink_lower_right: StringProperty(name="Lower lid right")
    eye_left: StringProperty(name="Left eye group", description="Vertex group of the left eyeball")
    eye_right: StringProperty(name="Right eye group", description="Vertex group of the right eyeball")
    ft_overwrite: BoolProperty(name="Overwrite", default=False,
                               description="Rebuild face tracking shapes that already exist instead of keeping them")
    # Optimization: what Auto optimize runs. The first three only remove what nothing uses, so they start on.
    opt_keys: BoolProperty(name="Empty shape keys", default=True, description="Delete shape keys that move nothing (vrc.*, KAT.* and face tracking keys stay)")
    opt_groups: BoolProperty(name="Unused vertex groups", default=True, description="Delete vertex groups no vertex has weight in")
    opt_materials: BoolProperty(name="Duplicate materials", default=True, description="Merge .001 duplicates, remove empty slots and orphans")
    opt_weights: BoolProperty(name="Limit weights to 4", default=False, description="Keep the 4 strongest bone weights per vertex")
    opt_bones: BoolProperty(name="Unused bones", default=False, description="Delete bones nothing uses. Humanoid bones stay")
    opt_join: BoolProperty(name="Join meshes", default=False, description="Join every mesh except the listed outfits, which must stay apart to toggle")
    opt_triangles: BoolProperty(name="Reduce triangles", default=False, description="Cut triangles on the meshes Reduce triangles would pick, to the chosen budget")
    opt_textures: BoolProperty(name="Shrink textures", default=False, description="Make smaller copies of textures above the largest size")
    opt_atlas: BoolProperty(name="Combine materials", default=False, description="Pack materials into atlases")
    opt_merge_ratio: FloatProperty(name="Merge ratio", default=0.5, min=0.0, max=1.0, subtype="FACTOR",
                                   description="Share of the loose bones (hair, tails, skirts) to fold into their parents")
    opt_merge_chain: EnumProperty(name="Chain", items=lambda self, context: chain_items(),
                                  description="Which loose chain of bones to thin out: all of them, or one (an ear, a tail)")
    opt_merge_into: EnumProperty(name="Weights go to", items=[("PARENT", "Parents", "Each bone's weights move to its parent"),
                                                              ("ACTIVE", "Active bone", "All the weights move to the active bone")],
                                 default="PARENT")
    opt_merge_keep: BoolProperty(name="Keep merged bones", default=False,
                                 description="Move the weights but leave the bones where they are")
    opt_merge_visible: BoolProperty(name="Visible meshes only", default=False,
                                    description="Only the weights on meshes you can see are moved")
    opt_del_bones: BoolProperty(name="Bones without weights", default=False,
                                description="Delete bones nothing uses: no weights, not humanoid, no constraints, drivers or parented objects")
    opt_del_constraints: BoolProperty(name="Constraints", default=False, description="Remove the constraints from the bones")
    opt_del_groups: BoolProperty(name="Vertex groups without weights", default=True,
                                 description="Delete vertex groups no vertex has weight in")
    opt_keep_twist: BoolProperty(name="Keep twist bones", default=True, description="Bones with twist in their name are not deleted")
    opt_skip_hidden: BoolProperty(name="Skip hidden bones", default=True, description="Bones you have hidden are not deleted")
    ft_synthesize: BoolProperty(name="Approximate the rest", default=True,
                                description="Make the shapes the model has no match for from its mouth, jaw, tongue and eye shapes and regions. They point the right way and are a starting point to sculpt over")
    tex_limit: EnumProperty(name="Largest texture", items=[(str(s), f"{s} px", "") for s in (256, 512, 1024, 2048, 4096)], default="2048")
    tex_pot: BoolProperty(name="Round to powers of two", default=True,
                          description="Unity compresses textures whose sides are powers of two")
    platform: EnumProperty(name="Platform", items=[("PC", "PC", "VRChat PC performance ranks"),
                                                    ("QUEST", "Quest", "VRChat Android / Quest performance ranks")], default="PC")
    prop_origin: EnumProperty(name="Origin", items=[("BOTTOM", "Bottom centre", ""), ("CENTER", "Centre", ""), ("KEEP", "Leave", "")], default="BOTTOM")
    prop_collision: EnumProperty(name="Collision", items=[("NONE", "None", ""), ("BOX", "Box", ""), ("CONVEX", "Convex hull", "")], default="CONVEX")
    prop_role: EnumProperty(name="Role", items=[("static", "Static", ""), ("dynamic", "Dynamic", ""), ("pickup", "Pickup", "")], default="static")
    prop_lods: BoolProperty(name="Make LODs", default=False)
    atlas_loose: BoolProperty(name="Ignore transparency taken from the colour", default=True,
                              description="FBX imports often wire a material's alpha to its colour image, which only dims dark pixels. Treat those as opaque")
    atlas_size: EnumProperty(name="Atlas size", items=[(s, f"{s} px", "") for s in ("1024", "2048", "4096")], default="2048")
    ft_show_needed: BoolProperty(name="What is still needed", default=True)
    ft_fill_empty: BoolProperty(name="Add empty", default=False,
                                description="Also add the shapes that cannot be derived, as empty keys to sculpt later")


classes = (KAT_Options,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.kat = PointerProperty(type=KAT_Options)


def unregister():
    del bpy.types.Scene.kat
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
