import math
from functools import total_ordering
import heapq
import bpy
import bpy.props
import bpy.utils
import bmesh
from mathutils.kdtree import KDTree

bl_info = {
    "name": "Blue Noise Particles",
    "description": "",
    "author": "Adam Newgas",
    "version": (0, 0, 1),
    "blender": (2, 78, 0),
    "location": "",
    "warning": "",
    "wiki_url": "",
    "category": ""}


@total_ordering
class HeapItem:
    def __init__(self, weight, index):
        self.weight = weight
        self.index = index

    def __lt__(self, other):
        return self.weight > other.weight

class SampleEliminator:
    def __init__(self, locations, target_samples, is_volume, mesh_area=None):
        self.locations = locations

        # Setup a KD Tree of all lcations
        self.tree = KDTree(len(locations))
        for index, location in locations.items():
            self.tree.insert(location, index)
        self.tree.balance()

        self.alpha = 8
        self.target_samples = target_samples
        self.current_samples = len(self.locations)

        M = self.current_samples
        N = self.target_samples

        # Choose rmax via heuristic
        bounds = [max(p[i] for p in locations.values()) - min(p[i] for p in locations.values())
                  for i in range(3)]

        A = bounds[0] * bounds[1] * bounds[2]
        self.rmax = (A / 4 / math.sqrt(2) / N) ** (1 / 3) # Volume based constraint
        if not is_volume and mesh_area is not None:
            # If we are constrained to 2d surface, then it is possible to
            # get a better bound for rmax. Depends on the mesh geometry.
            rmax2 = math.sqrt(mesh_area / 2 / math.sqrt(3) / N)
            self.rmax = min(self.rmax, rmax2)

        # Choose rmin via heuristic
        gamma = 1.5
        beta = 0.65
        self.rmin = self.rmax * (1 - (N / M) ** gamma) * beta

        # Build initial heap
        self.heap = []
        self.heap_items = {}
        for index, location in locations.items():
            tot_weight = 0
            for location2, index2, d in self.tree.find_range(location, 2 * self.rmax):
                tot_weight += self.w(d)
            item = HeapItem(tot_weight, index)
            self.heap_items[index] = item
            heapq.heappush(self.heap, item)

    def eliminate_one(self):
        item = heapq.heappop(self.heap)
        index = item.index
        location = self.locations[index]
        for location2, index2, d in self.tree.find_range(location, 2 * self.rmax):
            item2 = self.heap_items[index2]
            item2.weight -= self.w(d)
        # A better heap implementation would have an update_key operation
        heapq.heapify(self.heap)
        self.current_samples -= 1

    def eliminate(self):
        while self.current_samples > self.target_samples:
            self.eliminate_one()

    def get_indices(self):
        return (item.index for item in self.heap)

    def d(self, i, j):
        li = self.locations[i]
        lj = self.locations[j]
        return math.sqrt((li[0] - lj[0]) ** 2 +
                         (li[1] - lj[1]) ** 2 +
                         (li[2] - lj[2]) ** 2)

    def adj_d(self, d):
        return min(d, 2 * self.rmax)

    def w(self, d):
        return (1 - self.adj_d(d) / 2 / self.rmax) ** self.alpha


def get_mesh_area(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    area = sum(f.calc_area() for f in bm.faces)
    return area

class BlueNoiseParticles(bpy.types.Operator):
    bl_idname = "object.blue_noise_particles_operator"
    bl_label = "Blue Noise Particles"
    bl_options = {'REGISTER', 'UNDO'}

    emit_from_types = [("VERT", "Verts", "Emit from vertices"),
                       ("FACE", "Faces", "Emit from faces"),
                       ("VOLUME", "Volume", "Emit from volume")]
    emit_from = bpy.props.EnumProperty(items=emit_from_types,
                                       name="Emit From",
                                       description="Controls where particles are generated",
                                       default="FACE")

    quality_types = [("1.5", "Low", ""),
                       ("2", "Medium", ""),
                       ("5", "High", "")]
    quality = bpy.props.EnumProperty(items=quality_types,
                                     name="Quality",
                                     description="Controls how much oversampling is done",
                                     default="2")

    count = bpy.props.IntProperty(name="Count",
                                  description="Number of particles to emit",
                                  default=1000,
                                  min=0)

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ((ob is not None) and
                (ob.mode == "OBJECT") and
                (ob.type == "MESH") and
                (context.mode == "OBJECT"))

    def execute(self, context):
        obj = context.active_object  # type: bpy.types.Object
        scene = context.scene

        initial_particle_count = self.count * float(self.quality)

        # Create a new particle system
        bpy.ops.object.particle_system_add()
        psys = obj.particle_systems[-1]  # type: bpy.types.ParticleSystem
        pset = psys.settings
        pset.count = initial_particle_count
        pset.emit_from = self.emit_from

        # Force a scene update (generates particle loations)
        scene.update()

        is_volume = self.emit_from == 'VOLUME'
        mesh_area = None
        if not is_volume:
            mesh_area = get_mesh_area(obj)

        # Run sample elimination
        particles = psys.particles
        locations = dict((index, particle.location) for (index, particle) in particles.items())
        se = SampleEliminator(locations, self.count, is_volume, mesh_area)
        se.eliminate()
        alive_indices = se.get_indices()
        alive_locations = [locations[i] for i in alive_indices]

        # Delete particle system
        bpy.ops.object.particle_system_remove()

        # Create a new object, with vertices according the the alive locations
        me = bpy.data.meshes.new(obj.name + " ParticleMesh")
        ob = bpy.data.objects.new(obj.name + " Particles", me)
        scene.objects.link(ob)
        me.from_pydata(alive_locations, [], [])
        me.update()

        # Select new object
        scene.objects.active = ob
        obj.select = False
        ob.select = True

        # Add a particle system to the new object
        bpy.ops.object.particle_system_add()
        psys = ob.particle_systems[-1]  # type: bpy.types.ParticleSystem
        pset = psys.settings
        pset.count = self.count
        pset.emit_from = 'VERT'
        pset.use_emit_random = False
        pset.frame_start = 0
        pset.frame_end = 0
        pset.use_render_emitter = False

        return {'FINISHED'}

def menu_func(self, context):
    self.layout.operator(BlueNoiseParticles.bl_idname,
                         text="Blue Noise Particles",
                         icon='PLUGIN')


def register():
    bpy.utils.register_module(__name__)
    bpy.types.INFO_MT_curve_add.append(menu_func)


def unregister():
    bpy.types.INFO_MT_curve_add.remove(menu_func)
    bpy.utils.unregister_module(__name__)

if __name__ == "__main__":
    register()