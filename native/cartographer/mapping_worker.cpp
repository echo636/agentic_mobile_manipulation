// Online probability mapping using Cartographer's native range inserter.
// Pose is supplied by the ideal localizer; this is not scan-matching SLAM.
// Sensor origins and hit/miss endpoints are expressed in the robot base frame.
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <vector>
#include <boost/property_tree/json_parser.hpp>
#include <boost/property_tree/ptree.hpp>
#include "cartographer/mapping/2d/probability_grid.h"
#include "cartographer/mapping/2d/probability_grid_range_data_inserter_2d.h"
#include "cartographer/mapping/value_conversion_tables.h"
#include "cartographer/mapping/internal/2d/ray_to_pixel_mask.h"
#include "cartographer/sensor/range_data.h"

#if !defined(EIGEN_WORLD_VERSION) || EIGEN_WORLD_VERSION != 3
#error "This Cartographer runtime requires Eigen 3 headers matching the pinned static library"
#endif

using Tree = boost::property_tree::ptree;
namespace cm = cartographer::mapping;
using Key = std::pair<int, int>;

Key Cell(const Eigen::Vector3f& p, const Eigen::Vector2d& anchor, double resolution) {
  return {int(std::floor((p.x() - anchor.x()) / resolution)),
          int(std::floor((p.y() - anchor.y()) / resolution))};
}

// Reuse Cartographer's exact subpixel ray mask and cell-boundary convention.
// The anchor-based keys remain stable when the probability grid grows.
void RecordRay(const Eigen::Vector3f& origin, const Eigen::Vector3f& end,
               const cm::MapLimits& limits, const Eigen::Vector2d& anchor,
               bool hit, std::set<Key>* observed_free, std::set<Key>* observed_hits) {
  constexpr int scale = 1000;
  cm::MapLimits scaled(limits.resolution() / scale, limits.max(),
      cm::CellLimits(limits.cell_limits().num_x_cells * scale,
                     limits.cell_limits().num_y_cells * scale));
  const auto begin = scaled.GetCellIndex(origin.head<2>());
  const auto finish = scaled.GetCellIndex(end.head<2>());
  auto key_for = [&](const Eigen::Array2i& index) {
    const auto center = limits.GetCellCenter(index);
    return Cell({float(center.x()), float(center.y()), 0.f}, anchor, limits.resolution());
  };
  if (hit) observed_hits->insert(key_for(finish / scale));
  for (const auto& index : cm::RayToPixelMask(begin, finish, scale))
    observed_free->insert(key_for(index));
}

std::vector<double> Numbers(const Tree& values, size_t expected) {
  std::vector<double> result;
  for (const auto& item : values) {
    double value = item.second.get_value<double>();
    if (!std::isfinite(value)) throw std::runtime_error("Nonfinite input coordinate");
    result.push_back(value);
  }
  if (result.size() != expected) throw std::runtime_error("Invalid coordinate dimension");
  return result;
}

Eigen::Vector3f World(const Tree& point, const std::vector<double>& pose) {
  const auto p = Numbers(point, 3);
  const double c = std::cos(pose[2]), s = std::sin(pose[2]);
  return {float(pose[0] + c * p[0] - s * p[1]),
          float(pose[1] + s * p[0] + c * p[1]), 0.f};
}

int main(int argc, char** argv) {
  if (argc != 3) {
    std::cerr << "usage: mas_cartographer_worker OUTPUT_DIRECTORY RESOLUTION\n";
    return 2;
  }
  try {
    const std::filesystem::path output(argv[1]);
    const double resolution = std::stod(argv[2]);
    if (!std::isfinite(resolution) || resolution <= 0) throw std::runtime_error("Invalid resolution");
    std::filesystem::create_directories(output);
    cm::ValueConversionTables tables;
    std::unique_ptr<cm::ProbabilityGrid> grid;
    Eigen::Vector2d anchor;
    std::map<Key, int> recent_hits;
    std::set<Key> cleared_hits;
    bool ever_observed = false;
    cm::proto::ProbabilityGridRangeDataInserterOptions2D options;
    options.set_insert_free_space(true);
    options.set_hit_probability(0.55);
    options.set_miss_probability(0.49);
    cm::ProbabilityGridRangeDataInserter2D inserter(options);
    int64_t previous_sequence = -1;
    std::string line;
    while (std::getline(std::cin, line)) {
      Tree packet;
      std::istringstream input(line);
      boost::property_tree::read_json(input, packet);
      if (packet.get<bool>("close", false)) break;
      const auto sequence = packet.get<int64_t>("sequence");
      if (sequence <= previous_sequence) throw std::runtime_error("Non-increasing sequence");
      const auto pose = Numbers(packet.get_child("pose"), 3);
      const double timestamp = packet.get<double>("timestamp");
      if (!std::isfinite(timestamp)) throw std::runtime_error("Invalid timestamp");
      if (!grid) {
        anchor = Eigen::Vector2d(pose[0], pose[1]);
        // Empty unknown map around the first observed pose; never load a scene map.
        grid = std::make_unique<cm::ProbabilityGrid>(cm::MapLimits(
            resolution, Eigen::Vector2d(pose[0] + 32 * resolution, pose[1] + 32 * resolution),
            cm::CellLimits(64, 64)), &tables);
      }
      size_t returns_count = 0, misses_count = 0, sensors_count = 0;
      std::set<Key> observed_free, observed_hits;
      for (const auto& item : packet.get_child("scans")) {
        const Tree& scan = item.second;
        cartographer::sensor::RangeData data;
        data.origin = World(scan.get_child("origin"), pose);
        for (const auto& point : scan.get_child("returns"))
          data.returns.push_back({World(point.second, pose)});
        for (const auto& point : scan.get_child("misses"))
          data.misses.push_back({World(point.second, pose)});
        returns_count += data.returns.size();
        misses_count += data.misses.size();
        if (data.returns.empty() && data.misses.empty()) continue;
        if (std::getenv("MAS_CARTOGRAPHER_DEBUG")) {
          std::cerr << "sequence=" << sequence << " origin=" << data.origin.transpose()
                    << " limits=" << grid->limits().max().transpose()
                    << " dimensions=" << grid->limits().cell_limits().num_x_cells << ',' << grid->limits().cell_limits().num_y_cells << '\n';
          for (const auto& point : data.returns) std::cerr << "hit=" << point.position.transpose() << '\n';
          for (const auto& point : data.misses) std::cerr << "miss=" << point.position.transpose() << '\n';
        }
        // Insert first so limits include every endpoint. Mirror its exact ray
        // raster for the dynamic layer; all cameras' hits win at capture end.
        inserter.Insert(data, grid.get());
        for (const auto& point : data.returns)
          RecordRay(data.origin, point.position, grid->limits(), anchor, true, &observed_free, &observed_hits);
        for (const auto& point : data.misses)
          RecordRay(data.origin, point.position, grid->limits(), anchor, false, &observed_free, &observed_hits);
        ++sensors_count;
        ever_observed = true;
      }
      for (const auto& key : observed_free) {
        auto it = recent_hits.find(key);
        if (it != recent_hits.end() && observed_hits.count(key) == 0 && --it->second <= 0) {
          cleared_hits.insert(key);
          recent_hits.erase(it);
        }
      }
      for (const auto& key : observed_hits) { recent_hits[key] = 3; cleared_hits.erase(key); }
      Eigen::Array2i offset;
      cm::CellLimits extent;
      if (ever_observed) grid->ComputeCroppedLimits(&offset, &extent);
      else { offset = Eigen::Array2i::Zero(); extent = grid->limits().cell_limits(); }
      // Cartographer indices are reverse Y/X. Export increasing world Y/X.
      const int width = extent.num_y_cells, height = extent.num_x_cells;
      if (width < 1 || height < 1) throw std::runtime_error("Empty grid limits");
      const Eigen::Array2i first_cell = offset + Eigen::Array2i(height - 1, width - 1);
      // GetCellCenter returns float32. Exporting it would shift the world lattice
      // by sub-micrometres whenever the cropped bounds change. Preserve the
      // MapLimits double precision so a fixed world goal stays in one lattice.
      const Eigen::Vector2d origin(
          grid->limits().max().x() - resolution * (first_cell.y() + .5),
          grid->limits().max().y() - resolution * (first_cell.x() + .5));
      const std::string stem = "occupancy_" + std::to_string(sequence);
      const auto raw = output / (stem + ".bin");
      std::ofstream cells(raw, std::ios::binary);
      size_t free_count = 0, occupied_count = 0, unknown_count = 0;
      for (int row = 0; row < height; ++row) {
        for (int col = 0; col < width; ++col) {
          const Eigen::Array2i index = offset + Eigen::Array2i(height - 1 - row, width - 1 - col);
          uint8_t value = 255;
          if (grid->IsKnown(index)) {
            const float probability = grid->GetProbability(index);
            if (probability > 0.5f) value = 100;
            else if (probability < 0.5f) value = 0;
          }
          const auto center = grid->limits().GetCellCenter(index);
          const auto key = Cell({float(center.x()), float(center.y()), 0.f}, anchor, resolution);
          if (cleared_hits.count(key)) value = 0;
          if (recent_hits.count(key)) value = 100;
          if (value == 0) ++free_count;
          else if (value == 100) ++occupied_count;
          else ++unknown_count;
          cells.put(static_cast<char>(value));
        }
      }
      cells.close();
      if (!cells) throw std::runtime_error("Cannot save occupancy cells");
      std::ostringstream response;
      response.precision(17);
      response << "{\"sequence\":" << sequence << ",\"timestamp\":" << timestamp
               << ",\"width\":" << width << ",\"height\":" << height
               << ",\"resolution\":" << resolution << ",\"origin\":[" << origin.x() << ',' << origin.y()
               << "],\"file\":\"" << stem << ".bin\",\"counts\":{\"free\":" << free_count
               << ",\"occupied\":" << occupied_count << ",\"unknown\":" << unknown_count
               << "},\"returns\":" << returns_count << ",\"misses\":" << misses_count
               << ",\"sensors\":" << sensors_count
               << ",\"dynamic_hit_cells\":" << recent_hits.size()
               << ",\"dynamic_cleared_cells\":" << cleared_hits.size() << '}';
      std::ofstream(output / (stem + ".json")) << response.str() << '\n';
      previous_sequence = sequence;
      std::cout << response.str() << std::endl;
    }
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "mapping_error: " << error.what() << '\n';
    return 1;
  }
}
