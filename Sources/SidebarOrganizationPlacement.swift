import Foundation

/// Native membership provenance, separate from classification and manual tags.
/// An absent legacy value is manual. Only a retained native plan creates automatic provenance.
struct SidebarOrganizationPlacement: Codable, Equatable, Sendable {
    enum Origin: String, Codable, Sendable { case manual, automatic }
    let origin: Origin
    let planID: UUID?

    init(planID: UUID) {
        origin = .automatic
        self.planID = planID
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        origin = try values.decode(Origin.self, forKey: .origin)
        planID = try values.decodeIfPresent(UUID.self, forKey: .planID)
        guard (origin == .automatic) == (planID != nil) else {
            throw DecodingError.dataCorruptedError(forKey: .planID, in: values,
                debugDescription: "Automatic placement requires a native plan identity")
        }
    }
}
